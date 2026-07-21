"""Tests for public-evidence App Group association planning."""

from __future__ import annotations

from sideloadedipa.adapters.apple import (
    app_group_association_verified,
    app_group_requirement,
)
from sideloadedipa.apple_planning import plan_apple_resources
from sideloadedipa.domain import (
    AppleCapabilityState,
    AppleStateSnapshot,
    FrozenJsonObject,
    OperationDisposition,
    freeze_json,
)


def setting(group_identifier: str, enabled: bool = True) -> FrozenJsonObject:
    value = freeze_json(
        {
            "key": "APP_GROUPS",
            "options": [{"key": group_identifier, "enabled": enabled}],
        }
    )
    assert isinstance(value, FrozenJsonObject)
    return value


def capability(
    resource_id: str = "CAP_GROUPS",
    *,
    settings: tuple[FrozenJsonObject, ...] = (),
) -> AppleCapabilityState:
    return AppleCapabilityState(resource_id, "BUNDLE_ONE", "APP_GROUPS", settings)


def snapshot(*capabilities: AppleCapabilityState) -> AppleStateSnapshot:
    return AppleStateSnapshot("digest", (), tuple(capabilities), (), (), ())


def operation_for(state: AppleStateSnapshot, group_identifier: str = "group.io.example"):
    requirement = app_group_requirement(
        snapshot=state,
        bundle_resource_id="BUNDLE_ONE",
        bundle_id="io.example.app",
        group_identifier=group_identifier,
    )
    return plan_apple_resources(
        task_name="Example", snapshot_sha256="digest", requirements=(requirement,)
    ).operations[0]


def test_accepts_only_exact_enabled_public_option_evidence() -> None:
    exact = capability(settings=(setting("group.io.example"),))

    assert app_group_association_verified(exact, "group.io.example") is True
    assert app_group_association_verified(exact, "group.io.other") is False
    assert (
        app_group_association_verified(
            capability(settings=(setting("group.io.example", enabled=False),)),
            "group.io.example",
        )
        is False
    )


def test_verified_association_is_no_op() -> None:
    operation = operation_for(snapshot(capability(settings=(setting("group.io.example"),))))

    assert operation.disposition is OperationDisposition.NO_OP
    assert operation.existing_resource_id == "CAP_GROUPS"


def test_unverifiable_association_requires_portal_or_xcode_action() -> None:
    for state in (
        snapshot(),
        snapshot(capability()),
        snapshot(capability(settings=(setting("OPAQUE_RESOURCE_ID"),))),
    ):
        operation = operation_for(state)
        assert operation.disposition is OperationDisposition.MANUAL_REQUIRED
        diagnostic = operation.diagnostics[0]
        assert diagnostic.bundle_id == "io.example.app"
        assert "Account Holder or Admin" in (diagnostic.remediation or "")
        assert "Developer Portal or Xcode" in (diagnostic.remediation or "")


def test_duplicate_app_groups_capabilities_are_blocked() -> None:
    operation = operation_for(
        snapshot(
            capability("CAP_ONE", settings=(setting("group.io.example"),)),
            capability("CAP_TWO", settings=(setting("group.io.example"),)),
        )
    )

    assert operation.disposition is OperationDisposition.BLOCKED
    assert operation.diagnostics[0].code == "apple.resource_ambiguous"


def test_malformed_setting_is_not_treated_as_association_evidence() -> None:
    values = [
        freeze_json("not-an-object"),
        freeze_json({"key": "APP_GROUPS", "options": "not-a-list"}),
        freeze_json({"key": "APP_GROUPS", "options": ["not-an-object"]}),
    ]
    state = capability(settings=tuple(values))

    assert app_group_association_verified(state, "group.io.example") is False
