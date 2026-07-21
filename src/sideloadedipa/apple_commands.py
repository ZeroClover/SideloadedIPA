"""Read-only Apple planning and explicitly applied profile synchronization."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, cast

from sideloadedipa.adapters.apple import (
    AppleStateCollector,
    AscBundleIdGateway,
    AscCapabilityGateway,
    AscClient,
    AscProfileGateway,
    BundleIdReconciler,
    CapabilityAutomation,
    CapabilityReconciler,
    MobileProvisionValidator,
    ProfileReconciler,
    ProfileReconciliationResult,
    ProfileSyncRequest,
    app_group_requirement,
    bundle_id_requirement,
    capability_requirement,
    capability_rule,
    exact_bundle_id_matches,
)
from sideloadedipa.apple_intents import BundleResourceIntent, derive_bundle_resource_intents
from sideloadedipa.apple_planning import plan_apple_resources
from sideloadedipa.apple_state_probe import certificate_identity_from_environment
from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.config import (
    EntitlementTemplateContext,
    load_configuration,
    load_entitlement_template,
)
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleOperation,
    AppleResourceKind,
    AppleResourcePlan,
    AppleResourceRequirement,
    AppleStateSnapshot,
    CertificateIdentity,
    EntitlementMode,
    FrozenJsonObject,
    OperationDisposition,
    P12CertificateIdentity,
    ProfileManifestEntry,
    ProfileValidationRequest,
    Task,
    TaskConfiguration,
    freeze_json,
    normalize_entitlements,
    thaw_json,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.profile_storage import (
    build_profile_manifest,
    profile_relative_path,
    store_profile,
    store_profile_manifest,
)

_PROFILE_REFRESH_THRESHOLD = timedelta(days=30)
_IOS_DEVICE_CLASSES = frozenset({"IPHONE", "IPAD"})


class AppleCommandBackend(Protocol):
    def collect(self) -> AppleStateSnapshot: ...

    def resolve_certificate(self, snapshot: AppleStateSnapshot) -> CertificateIdentity: ...

    def ensure_bundle(self, intent: BundleResourceIntent) -> AppleBundleIdentifierState: ...

    def ensure_capability(
        self,
        *,
        bundle: AppleBundleIdentifierState,
        capability_type: str,
    ) -> None: ...

    def ensure_profile(
        self,
        *,
        task: Task,
        intent: BundleResourceIntent,
        snapshot: AppleStateSnapshot,
        certificate: CertificateIdentity,
        bundle: AppleBundleIdentifierState,
        config_path: Path,
    ) -> ProfileReconciliationResult: ...


@dataclass(frozen=True, slots=True)
class AppleCommandDependencies:
    load: Callable[[Path], TaskConfiguration] = load_configuration
    backend: AppleCommandBackend | None = None
    profile_root: Path = Path("work/profiles")


class AscAppleCommandBackend:
    def __init__(self, client: AscClient | None = None) -> None:
        self.client = client or AscClient()
        self.bundle_ids = BundleIdReconciler(AscBundleIdGateway(self.client))
        self.capabilities = CapabilityReconciler(AscCapabilityGateway(self.client))
        self.profiles = AscProfileGateway(self.client)

    def collect(self) -> AppleStateSnapshot:
        return AppleStateCollector(self.client).collect()

    def resolve_certificate(self, snapshot: AppleStateSnapshot) -> CertificateIdentity:
        identity = certificate_identity_from_environment(snapshot)
        if identity is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_MISSING,
                "Apple resource commands require the development certificate P12",
                remediation=(
                    "set APPLE_DEV_CERT_P12_ENCODED and APPLE_DEV_CERT_PASSWORD in the CI environment"
                ),
            )
        return identity

    def ensure_bundle(self, intent: BundleResourceIntent) -> AppleBundleIdentifierState:
        return self.bundle_ids.ensure(
            identifier=intent.target_bundle_id,
            name=intent.display_name,
        )

    def ensure_capability(
        self,
        *,
        bundle: AppleBundleIdentifierState,
        capability_type: str,
    ) -> None:
        self.capabilities.ensure(
            bundle_resource_id=bundle.resource_id,
            bundle_id=bundle.identifier,
            capability_type=capability_type,
        )

    def ensure_profile(
        self,
        *,
        task: Task,
        intent: BundleResourceIntent,
        snapshot: AppleStateSnapshot,
        certificate: CertificateIdentity,
        bundle: AppleBundleIdentifierState,
        config_path: Path,
    ) -> ProfileReconciliationResult:
        prefix = _application_identifier_prefix(bundle)
        expected = _expected_entitlements(
            task=task,
            intent=intent,
            team_id=certificate.team_id,
            app_identifier_prefix=prefix,
            config_path=config_path,
        )
        devices = tuple(
            device
            for device in snapshot.devices
            if device.status == "ENABLED" and device.device_class in _IOS_DEVICE_CLASSES
        )
        if not devices:
            raise DomainError(
                ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                "no enabled iPhone or iPad is available for a development profile",
                bundle_id=intent.target_bundle_id,
                remediation="register and enable an iPhone or iPad in the Apple Developer account",
            )
        validation = ProfileValidationRequest(
            resource_id="",
            target_bundle_id=intent.target_bundle_id,
            application_identifier=f"{prefix}{intent.target_bundle_id}",
            team_id=certificate.team_id,
            profile_type=intent.profile_type,
            certificate_sha256=certificate.certificate_sha256,
            device_udid_sha256=tuple(sorted(device.udid_sha256 for device in devices)),
            path=profile_relative_path(task.task_name, intent.target_bundle_id),
            expected_entitlements=normalize_entitlements(expected).values,
        )
        reconciler = ProfileReconciler(
            self.profiles,
            MobileProvisionValidator(
                now=datetime.now(timezone.utc),
                refresh_threshold=_PROFILE_REFRESH_THRESHOLD,
            ),
        )
        return reconciler.ensure(
            ProfileSyncRequest(
                base_name=intent.profile_name,
                bundle_resource_id=bundle.resource_id,
                certificate_resource_id=certificate.resource_id,
                device_resource_ids=tuple(sorted(device.resource_id for device in devices)),
                validation=validation,
            )
        )


def _selected_tasks(configuration: TaskConfiguration, names: tuple[str, ...]) -> tuple[Task, ...]:
    if not names:
        return configuration.tasks
    if len(set(names)) != len(names):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "Apple command task selection contains duplicates",
            remediation="pass each --task value once",
        )
    by_name = {task.task_name: task for task in configuration.tasks}
    missing = tuple(name for name in names if name not in by_name)
    if missing:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "Apple command task selection contains unknown task names",
            remediation="select task names declared in the configuration",
            safe_details=(("task_names", missing),),
        )
    return tuple(by_name[name] for name in names)


def _application_identifier_prefix(bundle: AppleBundleIdentifierState) -> str:
    if not bundle.seed_id:
        raise DomainError(
            ErrorCode.APPLE_RESOURCE_CONFLICT,
            "Apple Bundle ID state does not expose the App ID prefix",
            bundle_id=bundle.identifier,
            remediation=(
                "verify the explicit App ID in the Developer Portal; do not assume Team ID equals prefix"
            ),
        )
    prefix = bundle.seed_id.rstrip(".")
    if not prefix:
        raise DomainError(
            ErrorCode.ADAPTER_RESPONSE_INVALID,
            "Apple Bundle ID returned an empty App ID prefix",
            bundle_id=bundle.identifier,
        )
    return f"{prefix}."


def _profile_mode_entitlements(
    intent: BundleResourceIntent,
    team_id: str,
    app_identifier_prefix: str,
) -> dict[str, object]:
    application_identifier = f"{app_identifier_prefix}{intent.target_bundle_id}"
    values: dict[str, object] = {
        "application-identifier": application_identifier,
        "com.apple.developer.team-identifier": team_id,
        "get-task-allow": True,
    }
    capabilities = set(intent.required_capabilities)
    if intent.app_groups:
        values["com.apple.security.application-groups"] = list(intent.app_groups)
    if "HEALTHKIT" in capabilities:
        values["com.apple.developer.healthkit"] = True
    if "INCREASED_MEMORY_LIMIT" in capabilities:
        values["com.apple.developer.kernel.increased-memory-limit"] = True
    if "KEYCHAIN_SHARING" in capabilities:
        values["keychain-access-groups"] = [application_identifier]
    if "CLINICAL_HEALTH_RECORDS" in capabilities:
        values["com.apple.developer.healthkit.access"] = ["health-records"]
    if "HEALTHKIT_BACKGROUND_DELIVERY" in capabilities:
        values["com.apple.developer.healthkit.background-delivery"] = True
    return values


def _repository_root(config_path: Path) -> Path:
    resolved = config_path.resolve()
    if resolved.parent.name == "configs":
        return resolved.parent.parent
    return Path.cwd()


def _expected_entitlements(
    *,
    task: Task,
    intent: BundleResourceIntent,
    team_id: str,
    app_identifier_prefix: str,
    config_path: Path,
) -> dict[str, object]:
    policy = intent.entitlement_policy
    if policy.mode is EntitlementMode.PROFILE:
        return _profile_mode_entitlements(intent, team_id, app_identifier_prefix)
    if policy.mode is EntitlementMode.PRESERVE_SOURCE:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "profile synchronization cannot authorize preserve-source entitlements without inventory",
            task_name=task.task_name,
            bundle_id=intent.target_bundle_id,
            remediation="run inspect/sign planning with source entitlements or use a reviewed template",
        )
    if policy.template_path is None:
        raise ConfigurationError(
            ErrorCode.ENTITLEMENTS_TEMPLATE_MISSING,
            "template entitlement policy has no template path",
            task_name=task.task_name,
            bundle_id=intent.target_bundle_id,
        )
    return load_entitlement_template(
        _repository_root(config_path),
        policy.template_path,
        EntitlementTemplateContext(
            team_id=team_id,
            app_identifier_prefix=app_identifier_prefix,
            target_bundle_id=intent.target_bundle_id,
            app_groups=task.signing.app_groups if task.signing is not None else (),
        ),
    )


def _exact_bundle(
    snapshot: AppleStateSnapshot, target_bundle_id: str
) -> AppleBundleIdentifierState | None:
    matches = exact_bundle_id_matches(snapshot.bundle_ids, target_bundle_id)
    if len(matches) > 1:
        raise DomainError(
            ErrorCode.APPLE_RESOURCE_CONFLICT,
            "multiple App IDs match one configured target",
            bundle_id=target_bundle_id,
            remediation="resolve duplicate App IDs before profile synchronization",
            safe_details=(("resource_ids", tuple(value.resource_id for value in matches)),),
        )
    return matches[0] if matches else None


def _profile_requirement(
    intent: BundleResourceIntent,
) -> AppleResourceRequirement:
    blocked = intent.entitlement_policy.mode is EntitlementMode.PRESERVE_SOURCE
    return AppleResourceRequirement(
        resource_kind=AppleResourceKind.PROFILE,
        action="validate-or-reconcile-profile",
        target=intent.profile_name,
        bundle_id=intent.target_bundle_id,
        matching_resource_ids=(),
        missing_disposition=(
            OperationDisposition.BLOCKED if blocked else OperationDisposition.SAFE_AUTOMATIC
        ),
        remediation=(
            "materialize source entitlements from inspected inventory before profile synchronization"
            if blocked
            else "validate an existing profile or create an additive replacement"
        ),
    )


def _requirements_for_task(
    task: Task,
    intents: tuple[BundleResourceIntent, ...],
    snapshot: AppleStateSnapshot,
    certificate: CertificateIdentity,
) -> tuple[AppleResourceRequirement, ...]:
    from sideloadedipa.certificate_identity import certificate_requirement

    requirements: list[AppleResourceRequirement] = [
        certificate_requirement(
            snapshot=snapshot,
            identity=_resolved_as_p12(certificate),
        )
    ]
    for intent in intents:
        requirements.append(
            bundle_id_requirement(
                snapshot=snapshot,
                identifier=intent.target_bundle_id,
                allow_creation=True,
            )
        )
        bundle = _exact_bundle(snapshot, intent.target_bundle_id)
        bundle_resource_id = bundle.resource_id if bundle is not None else ""
        for capability_type in intent.required_capabilities:
            requirements.append(
                capability_requirement(
                    snapshot=snapshot,
                    bundle_resource_id=bundle_resource_id,
                    bundle_id=intent.target_bundle_id,
                    capability_type=capability_type,
                )
            )
        for group in intent.app_groups:
            requirements.append(
                app_group_requirement(
                    snapshot=snapshot,
                    bundle_resource_id=bundle_resource_id,
                    bundle_id=intent.target_bundle_id,
                    group_identifier=group,
                )
            )
        requirements.append(_profile_requirement(intent))
    return tuple(requirements)


def _resolved_as_p12(identity: CertificateIdentity) -> P12CertificateIdentity:
    return P12CertificateIdentity(
        team_id=identity.team_id,
        serial_number=identity.serial_number,
        public_key_sha256=identity.public_key_sha256,
        certificate_sha256=identity.certificate_sha256,
        expires_at=identity.expires_at,
    )


def _operation_document(operation: AppleOperation) -> dict[str, object]:
    return {
        "disposition": operation.disposition.value,
        "resource_kind": operation.resource_kind.value,
        "action": operation.action,
        "target": operation.target,
        "bundle_id": operation.bundle_id,
        "existing_resource_id": operation.existing_resource_id,
        "diagnostics": [
            {
                "code": diagnostic.code,
                "severity": diagnostic.severity.value,
                "message": diagnostic.message,
                "remediation": diagnostic.remediation,
                "details": {key: thaw_json(value) for key, value in diagnostic.details},
            }
            for diagnostic in operation.diagnostics
        ],
    }


def _plan_document(
    *,
    command: str,
    apply: bool,
    snapshot: AppleStateSnapshot,
    certificate: CertificateIdentity,
    tasks: tuple[Task, ...],
    intents_by_task: dict[str, tuple[BundleResourceIntent, ...]],
    plans: dict[str, AppleResourcePlan],
    status: str | None = None,
    manifests: dict[str, tuple[str, str]] | None = None,
) -> dict[str, object]:
    counts = Counter(
        operation.disposition.value for plan in plans.values() for operation in plan.operations
    )
    blocked = (
        counts[OperationDisposition.BLOCKED.value]
        + counts[OperationDisposition.MANUAL_REQUIRED.value]
    )
    return {
        "schema_version": 1,
        "command": command,
        "apply": apply,
        "status": status or ("blocked" if blocked else "ready"),
        "snapshot_sha256": snapshot.snapshot_sha256,
        "certificate": {
            "resource_id": certificate.resource_id,
            "team_id": certificate.team_id,
            "certificate_sha256": certificate.certificate_sha256,
            "expires_at": certificate.expires_at.isoformat(),
        },
        "counts": dict(sorted(counts.items())),
        "tasks": [
            {
                "task_name": task.task_name,
                "bundle_count": len(intents_by_task[task.task_name]),
                "operations": [
                    _operation_document(operation) for operation in plans[task.task_name].operations
                ],
                "manifest": (
                    {
                        "path": manifests[task.task_name][0],
                        "sha256": manifests[task.task_name][1],
                    }
                    if manifests is not None and task.task_name in manifests
                    else None
                ),
            }
            for task in tasks
        ],
    }


def _plans(
    tasks: tuple[Task, ...],
    intents_by_task: dict[str, tuple[BundleResourceIntent, ...]],
    snapshot: AppleStateSnapshot,
    certificate: CertificateIdentity,
) -> dict[str, AppleResourcePlan]:
    return {
        task.task_name: plan_apple_resources(
            task_name=task.task_name,
            snapshot_sha256=snapshot.snapshot_sha256,
            requirements=_requirements_for_task(
                task,
                intents_by_task[task.task_name],
                snapshot,
                certificate,
            ),
        )
        for task in tasks
    }


def _human_report(document: dict[str, object]) -> str:
    counts = cast(dict[str, int], document["counts"])
    lines = [
        (
            f"Apple {document['command']}: {document['status']}; "
            f"{counts.get('no-op', 0)} no-op, "
            f"{counts.get('safe-automatic', 0)} automatic, "
            f"{counts.get('manual-required', 0)} manual, "
            f"{counts.get('blocked', 0)} blocked"
        )
    ]
    for task_document in cast(list[dict[str, object]], document["tasks"]):
        lines.append(f"Task {task_document['task_name']} ({task_document['bundle_count']} bundles)")
        for operation in cast(list[dict[str, object]], task_document["operations"]):
            bundle_suffix = (
                f" [{operation['bundle_id']}]" if operation["bundle_id"] is not None else ""
            )
            lines.append(
                f"  {operation['disposition']}: {operation['resource_kind']} "
                f"{operation['target']}{bundle_suffix}"
            )
            for diagnostic in cast(list[dict[str, object]], operation["diagnostics"]):
                remediation = diagnostic["remediation"]
                if remediation:
                    lines.append(f"    remediation: {remediation}")
        manifest = task_document["manifest"]
        if manifest is not None:
            manifest_document = cast(dict[str, object], manifest)
            lines.append(
                f"  manifest: {manifest_document['path']} " f"sha256={manifest_document['sha256']}"
            )
    return "\n".join(lines)


def _result(document: dict[str, object]) -> CommandResult:
    frozen = freeze_json(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("Apple command report root must be an object")
    return CommandResult(
        exit_code=0 if document["status"] in {"ready", "applied"} else 1,
        human_output=_human_report(document),
        payload=frozen.items,
    )


def _read_plan(
    request: CommandRequest,
    dependencies: AppleCommandDependencies,
    backend: AppleCommandBackend,
) -> tuple[
    tuple[Task, ...],
    dict[str, tuple[BundleResourceIntent, ...]],
    AppleStateSnapshot,
    CertificateIdentity,
    dict[str, AppleResourcePlan],
]:
    configuration = dependencies.load(request.config_path)
    tasks = _selected_tasks(configuration, request.task_names)
    intents_by_task = {task.task_name: derive_bundle_resource_intents(task) for task in tasks}
    snapshot = backend.collect()
    certificate = backend.resolve_certificate(snapshot)
    plans = _plans(tasks, intents_by_task, snapshot, certificate)
    return tasks, intents_by_task, snapshot, certificate, plans


def plan_command(
    request: CommandRequest,
    dependencies: AppleCommandDependencies = AppleCommandDependencies(),
) -> CommandResult:
    """Emit a complete read-only Apple resource plan."""

    backend = dependencies.backend or AscAppleCommandBackend()
    tasks, intents, snapshot, certificate, plans = _read_plan(request, dependencies, backend)
    return _result(
        _plan_document(
            command="plan",
            apply=False,
            snapshot=snapshot,
            certificate=certificate,
            tasks=tasks,
            intents_by_task=intents,
            plans=plans,
        )
    )


def _has_prerequisite_blockers(plans: dict[str, AppleResourcePlan]) -> bool:
    return any(
        operation.disposition
        in {OperationDisposition.MANUAL_REQUIRED, OperationDisposition.BLOCKED}
        for plan in plans.values()
        for operation in plan.operations
    )


def _store_reconciled_profiles(
    *,
    root: Path,
    tasks: tuple[Task, ...],
    snapshot: AppleStateSnapshot,
    results: dict[str, tuple[tuple[BundleResourceIntent, ProfileReconciliationResult], ...]],
    certificate: CertificateIdentity,
) -> dict[str, tuple[str, str]]:
    manifests: dict[str, tuple[str, str]] = {}
    for task in tasks:
        entries = []
        for intent, result in results[task.task_name]:
            relative_path, digest = store_profile(
                root,
                task_name=task.task_name,
                target_bundle_id=intent.target_bundle_id,
                content=result.content,
            )
            if digest != result.profile.profile_sha256 or relative_path != result.profile.path:
                raise DomainError(
                    ErrorCode.DOMAIN_INVARIANT,
                    "stored profile evidence differs from validated profile evidence",
                    task_name=task.task_name,
                    bundle_id=intent.target_bundle_id,
                )
            bundle = _exact_bundle(snapshot, intent.target_bundle_id)
            if bundle is None:
                raise DomainError(
                    ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                    "validated profile target App ID disappeared from final Apple state",
                    task_name=task.task_name,
                    bundle_id=intent.target_bundle_id,
                )
            device_set_sha256 = hashlib.sha256(
                json.dumps(
                    sorted(bundle_id for bundle_id in result.profile.device_ids),
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            entries.append(
                ProfileManifestEntry(
                    target_bundle_id=intent.target_bundle_id,
                    bundle_resource_id=bundle.resource_id,
                    profile_resource_id=result.profile.resource_id,
                    certificate_resource_id=certificate.resource_id,
                    profile_path=relative_path,
                    profile_sha256=digest,
                    device_set_sha256=device_set_sha256,
                    expires_at=result.profile.expires_at,
                )
            )
        manifest = build_profile_manifest(
            task_name=task.task_name,
            snapshot_sha256=snapshot.snapshot_sha256,
            entries=tuple(entries),
        )
        path = store_profile_manifest(root, manifest)
        manifests[task.task_name] = (path.as_posix(), manifest.manifest_sha256)
    return manifests


def sync_command(
    request: CommandRequest,
    dependencies: AppleCommandDependencies = AppleCommandDependencies(),
) -> CommandResult:
    """Plan by default; apply only additive Apple operations behind ``--apply``."""

    backend = dependencies.backend or AscAppleCommandBackend()
    tasks, intents, snapshot, certificate, plans = _read_plan(request, dependencies, backend)
    if not request.apply:
        return _result(
            _plan_document(
                command="sync",
                apply=False,
                snapshot=snapshot,
                certificate=certificate,
                tasks=tasks,
                intents_by_task=intents,
                plans=plans,
            )
        )

    for task in tasks:
        for intent in intents[task.task_name]:
            backend.ensure_bundle(intent)

    snapshot = backend.collect()
    for task in tasks:
        for intent in intents[task.task_name]:
            bundle = _exact_bundle(snapshot, intent.target_bundle_id)
            if bundle is None:
                raise DomainError(
                    ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                    "created App ID was not present after bundle reconciliation",
                    task_name=task.task_name,
                    bundle_id=intent.target_bundle_id,
                )
            for capability_type in intent.required_capabilities:
                if capability_rule(capability_type).automation is CapabilityAutomation.API_ADDITIVE:
                    backend.ensure_capability(
                        bundle=bundle,
                        capability_type=capability_type,
                    )

    snapshot = backend.collect()
    certificate = backend.resolve_certificate(snapshot)
    plans = _plans(tasks, intents, snapshot, certificate)
    if _has_prerequisite_blockers(plans):
        return _result(
            _plan_document(
                command="sync",
                apply=True,
                status="blocked",
                snapshot=snapshot,
                certificate=certificate,
                tasks=tasks,
                intents_by_task=intents,
                plans=plans,
            )
        )

    reconciled: dict[str, list[tuple[BundleResourceIntent, ProfileReconciliationResult]]] = {
        task.task_name: [] for task in tasks
    }
    for task in tasks:
        for intent in intents[task.task_name]:
            bundle = _exact_bundle(snapshot, intent.target_bundle_id)
            if bundle is None:
                raise DomainError(
                    ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                    "profile target App ID is absent after prerequisite reconciliation",
                    task_name=task.task_name,
                    bundle_id=intent.target_bundle_id,
                )
            reconciled[task.task_name].append(
                (
                    intent,
                    backend.ensure_profile(
                        task=task,
                        intent=intent,
                        snapshot=snapshot,
                        certificate=certificate,
                        bundle=bundle,
                        config_path=request.config_path,
                    ),
                )
            )

    final_snapshot = backend.collect()
    immutable_results = {key: tuple(value) for key, value in reconciled.items()}
    manifests = _store_reconciled_profiles(
        root=dependencies.profile_root,
        tasks=tasks,
        snapshot=final_snapshot,
        results=immutable_results,
        certificate=certificate,
    )
    final_plans = _plans(tasks, intents, final_snapshot, certificate)
    return _result(
        _plan_document(
            command="sync",
            apply=True,
            status="applied",
            snapshot=final_snapshot,
            certificate=certificate,
            tasks=tasks,
            intents_by_task=intents,
            plans=final_plans,
            manifests=manifests,
        )
    )
