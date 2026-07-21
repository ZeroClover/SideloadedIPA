"""Read-only App Group association verification from public capability state."""

from __future__ import annotations

from collections.abc import Mapping

from sideloadedipa.adapters.apple.capabilities import exact_capability_matches
from sideloadedipa.domain import (
    AppleCapabilityState,
    AppleResourceKind,
    AppleResourceRequirement,
    AppleStateSnapshot,
    FrozenJsonValue,
    OperationDisposition,
    thaw_json,
)


def _enabled_option_matches(value: FrozenJsonValue, group_identifier: str) -> bool:
    decoded = thaw_json(value)
    if not isinstance(decoded, Mapping):
        return False
    if decoded.get("key") != "APP_GROUPS":
        return False
    options = decoded.get("options")
    if not isinstance(options, list):
        return False
    return any(
        isinstance(option, Mapping)
        and option.get("key") == group_identifier
        and option.get("enabled") is True
        for option in options
    )


def app_group_association_verified(capability: AppleCapabilityState, group_identifier: str) -> bool:
    """Return true only for an exact enabled option in public API evidence."""

    return capability.capability_type == "APP_GROUPS" and any(
        _enabled_option_matches(setting, group_identifier) for setting in capability.settings
    )


def app_group_requirement(
    *,
    snapshot: AppleStateSnapshot,
    bundle_resource_id: str,
    bundle_id: str,
    group_identifier: str,
    manually_confirmed: bool = False,
) -> AppleResourceRequirement:
    capabilities = exact_capability_matches(snapshot.capabilities, bundle_resource_id, "APP_GROUPS")
    if len(capabilities) > 1:
        matching_resource_ids = tuple(value.resource_id for value in capabilities)
    else:
        matching_resource_ids = tuple(
            value.resource_id
            for value in capabilities
            if app_group_association_verified(value, group_identifier)
        )
    return AppleResourceRequirement(
        resource_kind=AppleResourceKind.APP_GROUP,
        action="verify-app-group-association",
        target=group_identifier,
        bundle_id=bundle_id,
        matching_resource_ids=matching_resource_ids,
        satisfied_without_resource=manually_confirmed and not matching_resource_ids,
        missing_disposition=OperationDisposition.MANUAL_REQUIRED,
        remediation=(
            f"as an Account Holder or Admin, register {group_identifier} if needed and "
            f"associate it with {bundle_id} in Developer Portal or Xcode, then re-run planning"
        ),
    )
