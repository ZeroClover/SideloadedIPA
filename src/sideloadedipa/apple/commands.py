"""Read-only Apple planning and explicitly applied profile synchronization."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.adapters.apple import ProfileReconciliationResult
from sideloadedipa.apple.backend import (
    AppleCommandBackend,
    AscAppleCommandBackend,
)
from sideloadedipa.apple.expected_entitlements import exact_bundle
from sideloadedipa.apple.intents import BundleResourceIntent, derive_bundle_resource_intents
from sideloadedipa.apple.reporting import (
    build_plans,
    command_result,
    plan_document,
)
from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    AppleResourcePlan,
    AppleStateSnapshot,
    CapabilityAutomation,
    CertificateIdentity,
    OperationDisposition,
    ProfileManifestEntry,
    Task,
    TaskConfiguration,
    capability_rule,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.pipeline.environment import selected_tasks
from sideloadedipa.signing.profile_storage import (
    build_profile_manifest,
    store_profile,
    store_profile_manifest,
)
from sideloadedipa.util.atomics import canonical_json

__all__ = [
    "AppleCommandBackend",
    "AppleCommandDependencies",
    "AscAppleCommandBackend",
    "plan_command",
    "sync_command",
]


@dataclass(frozen=True, slots=True)
class AppleCommandDependencies:
    load: Callable[[Path], TaskConfiguration] = load_configuration
    backend: AppleCommandBackend | None = None
    profile_root: Path = Path("work/profiles")
    record_created_resource: Callable[[str, str], None] | None = None


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
    tasks = selected_tasks(configuration, request.task_names, scope="Apple command")
    intents_by_task = {task.task_name: derive_bundle_resource_intents(task) for task in tasks}
    snapshot = backend.collect()
    certificate = backend.resolve_certificate(snapshot)
    plans = build_plans(tasks, intents_by_task, snapshot, certificate)
    return tasks, intents_by_task, snapshot, certificate, plans


def plan_command(
    request: CommandRequest,
    dependencies: AppleCommandDependencies = AppleCommandDependencies(),
) -> CommandResult:
    """Emit a complete read-only Apple resource plan."""

    backend = dependencies.backend or AscAppleCommandBackend()
    tasks, intents, snapshot, certificate, plans = _read_plan(request, dependencies, backend)
    return command_result(
        plan_document(
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
            bundle = exact_bundle(snapshot, intent.target_bundle_id)
            if bundle is None:
                raise DomainError(
                    ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                    "validated profile target App ID disappeared from final Apple state",
                    task_name=task.task_name,
                    bundle_id=intent.target_bundle_id,
                )
            device_set_sha256 = hashlib.sha256(
                canonical_json(sorted(result.profile.device_ids))
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
        return command_result(
            plan_document(
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
            existing = exact_bundle(snapshot, intent.target_bundle_id)
            ensured = backend.ensure_bundle(intent)
            if existing is None and dependencies.record_created_resource is not None:
                dependencies.record_created_resource("bundle-id", ensured.resource_id)

    snapshot = backend.collect()
    for task in tasks:
        for intent in intents[task.task_name]:
            bundle = exact_bundle(snapshot, intent.target_bundle_id)
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
    plans = build_plans(tasks, intents, snapshot, certificate)
    if _has_prerequisite_blockers(plans):
        return command_result(
            plan_document(
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
            bundle = exact_bundle(snapshot, intent.target_bundle_id)
            if bundle is None:
                raise DomainError(
                    ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                    "profile target App ID is absent after prerequisite reconciliation",
                    task_name=task.task_name,
                    bundle_id=intent.target_bundle_id,
                )
            result = backend.ensure_profile(
                task=task,
                intent=intent,
                snapshot=snapshot,
                certificate=certificate,
                bundle=bundle,
                config_path=request.config_path,
            )
            if result.created and dependencies.record_created_resource is not None:
                dependencies.record_created_resource("profile", result.profile.resource_id)
            reconciled[task.task_name].append((intent, result))

    final_snapshot = backend.collect()
    manifests = _store_reconciled_profiles(
        root=dependencies.profile_root,
        tasks=tasks,
        snapshot=final_snapshot,
        results={key: tuple(value) for key, value in reconciled.items()},
        certificate=certificate,
    )
    final_plans = build_plans(tasks, intents, final_snapshot, certificate)
    return command_result(
        plan_document(
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
