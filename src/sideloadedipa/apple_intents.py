"""Pure expansion of task configuration into Apple resource intents."""

from __future__ import annotations

from dataclasses import dataclass

from sideloadedipa.domain import (
    BundleRule,
    EntitlementMode,
    EntitlementPolicy,
    ProfileType,
    Task,
    derive_identifier_mappings,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode


@dataclass(frozen=True, slots=True)
class BundleResourceIntent:
    task_name: str
    display_name: str
    profile_name: str
    source_bundle_id: str
    target_bundle_id: str
    profile_type: ProfileType
    required_capabilities: tuple[str, ...]
    app_groups: tuple[str, ...]
    entitlement_policy: EntitlementPolicy


def _invalid(task: Task, message: str, remediation: str) -> ConfigurationError:
    return ConfigurationError(
        ErrorCode.CONFIG_INVALID,
        message,
        task_name=task.task_name,
        remediation=remediation,
    )


def _root_rule(task: Task) -> BundleRule:
    if task.signing is None:
        raise AssertionError("legacy tasks do not have an explicit root rule")
    candidates = tuple(rule for rule in task.signing.bundles if rule.role == "root")
    if len(candidates) != 1:
        raise _invalid(
            task,
            "multi-bundle Apple resource planning requires exactly one root bundle rule",
            "mark exactly one tasks.signing.bundles entry with role = 'root'",
        )
    return candidates[0]


def _nested_name(task: Task, rule: BundleRule) -> str:
    if rule.role == "root":
        return task.app_name
    component = rule.source_bundle_id.rsplit(".", maxsplit=1)[-1]
    return f"{task.app_name} {component}"


def derive_bundle_resource_intents(task: Task) -> tuple[BundleResourceIntent, ...]:
    """Expand configured identifier policy without reading or mutating Apple state."""

    if task.signing is None:
        return (
            BundleResourceIntent(
                task_name=task.task_name,
                display_name=task.app_name,
                profile_name=f"{task.app_name} Dev",
                source_bundle_id=task.bundle_id,
                target_bundle_id=task.bundle_id,
                profile_type=ProfileType.IOS_APP_DEVELOPMENT,
                required_capabilities=(),
                app_groups=(),
                entitlement_policy=EntitlementPolicy(mode=EntitlementMode.PROFILE),
            ),
        )

    if not task.signing.bundles:
        raise _invalid(
            task,
            "multi-bundle signing policy has no bundle rules",
            "declare one rule for every profile-bearing bundle",
        )
    root = _root_rule(task)
    if (
        root.target_bundle_id is not None
        and root.target_bundle_id.casefold() != task.bundle_id.casefold()
    ):
        raise _invalid(
            task,
            "root bundle rule target differs from the task bundle identifier",
            "set the root target_bundle_id to the task bundle_id or omit it",
        )
    source_keys = tuple(rule.source_bundle_id.casefold() for rule in task.signing.bundles)
    if len(set(source_keys)) != len(source_keys):
        raise _invalid(
            task,
            "multi-bundle signing policy contains duplicate source bundle rules",
            "keep exactly one rule for each source bundle identifier",
        )
    explicit = {
        rule.source_bundle_id: rule.target_bundle_id
        for rule in task.signing.bundles
        if rule.target_bundle_id is not None
    }
    mappings = derive_identifier_mappings(
        (rule.source_bundle_id for rule in task.signing.bundles),
        source_root_bundle_id=root.source_bundle_id,
        target_root_bundle_id=task.bundle_id,
        explicit_targets=explicit,
    )
    targets = {mapping.source_bundle_id: mapping.target_bundle_id for mapping in mappings}
    configured_groups = tuple(identifier for _, identifier in task.signing.app_groups)
    intents = []
    for rule in task.signing.bundles:
        display_name = _nested_name(task, rule)
        capabilities = tuple(sorted(set(rule.required_capabilities)))
        intents.append(
            BundleResourceIntent(
                task_name=task.task_name,
                display_name=display_name,
                profile_name=f"{display_name} Dev",
                source_bundle_id=rule.source_bundle_id,
                target_bundle_id=targets[rule.source_bundle_id],
                profile_type=task.signing.profile_type,
                required_capabilities=capabilities,
                app_groups=(configured_groups if "APP_GROUPS" in capabilities else ()),
                entitlement_policy=rule.entitlement_policy,
            )
        )
    return tuple(sorted(intents, key=lambda value: value.target_bundle_id.casefold()))
