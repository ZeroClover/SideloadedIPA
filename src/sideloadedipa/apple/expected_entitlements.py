"""Expected entitlement and Apple resource requirement derivation."""

from __future__ import annotations

from pathlib import Path

from sideloadedipa.adapters.apple import (
    app_group_requirement,
    bundle_id_requirement,
    capability_requirement,
    exact_bundle_id_matches,
)
from sideloadedipa.apple.intents import BundleResourceIntent
from sideloadedipa.config import EntitlementTemplateContext, load_entitlement_template
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleResourceKind,
    AppleResourceRequirement,
    AppleStateSnapshot,
    CertificateIdentity,
    EntitlementMode,
    OperationDisposition,
    P12CertificateIdentity,
    Task,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.signing.certificate_identity import certificate_requirement


def application_identifier_prefix(bundle: AppleBundleIdentifierState) -> str:
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


def expected_entitlements(
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


def exact_bundle(
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


def _profile_requirement(intent: BundleResourceIntent) -> AppleResourceRequirement:
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


def requirements_for_task(
    task: Task,
    intents: tuple[BundleResourceIntent, ...],
    snapshot: AppleStateSnapshot,
    certificate: CertificateIdentity,
) -> tuple[AppleResourceRequirement, ...]:
    identity = P12CertificateIdentity(
        team_id=certificate.team_id,
        serial_number=certificate.serial_number,
        public_key_sha256=certificate.public_key_sha256,
        certificate_sha256=certificate.certificate_sha256,
        expires_at=certificate.expires_at,
    )
    requirements: list[AppleResourceRequirement] = [
        certificate_requirement(snapshot=snapshot, identity=identity)
    ]
    manually_confirmed_groups = set(
        task.signing.manual_app_group_associations if task.signing is not None else ()
    )
    for intent in intents:
        requirements.append(
            bundle_id_requirement(
                snapshot=snapshot,
                identifier=intent.target_bundle_id,
                allow_creation=True,
            )
        )
        bundle = exact_bundle(snapshot, intent.target_bundle_id)
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
                    manually_confirmed=group in manually_confirmed_groups,
                )
            )
        requirements.append(_profile_requirement(intent))
    return tuple(requirements)
