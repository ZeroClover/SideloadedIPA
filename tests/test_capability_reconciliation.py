"""Tests for allowlisted additive Apple capability reconciliation."""

from __future__ import annotations

import pytest

from sideloadedipa.adapters.apple import (
    AscCapabilityGateway,
    AscResponse,
    CapabilityReconciler,
    capability_requirement,
)
from sideloadedipa.apple.planning import plan_apple_resources
from sideloadedipa.domain import (
    AppleCapabilityState,
    AppleStateSnapshot,
    CapabilityAutomation,
    FrozenJsonObject,
    OperationDisposition,
    capability_rule,
    freeze_json,
)
from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode


def capability(resource_id: str, capability_type: str) -> AppleCapabilityState:
    return AppleCapabilityState(
        resource_id=resource_id,
        bundle_resource_id="BUNDLE_ONE",
        capability_type=capability_type,
    )


def snapshot(*capabilities: AppleCapabilityState) -> AppleStateSnapshot:
    return AppleStateSnapshot("digest", (), tuple(capabilities), (), (), ())


class FakeGateway:
    def __init__(
        self,
        listings: list[tuple[AppleCapabilityState, ...]],
        add_result: AppleCapabilityState | AdapterError,
    ) -> None:
        self.listings = listings
        self.add_result = add_result
        self.add_calls: list[tuple[str, str]] = []

    def list(self, bundle_resource_id: str) -> tuple[AppleCapabilityState, ...]:
        assert bundle_resource_id == "BUNDLE_ONE"
        return self.listings.pop(0)

    def add(self, bundle_resource_id: str, capability_type: str) -> AppleCapabilityState:
        self.add_calls.append((bundle_resource_id, capability_type))
        if isinstance(self.add_result, AdapterError):
            raise self.add_result
        return self.add_result


class RecordingClient:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def run_json(
        self,
        args: tuple[str, ...],
        *,
        paginate: bool = False,
        allow_empty: bool = False,
    ) -> AscResponse:
        self.calls.append((args, paginate))
        value = freeze_json(self.documents.pop(0))
        assert isinstance(value, FrozenJsonObject)
        return AscResponse(value, ("asc", *args), 0.01)


def test_registry_encodes_verified_automation_boundaries() -> None:
    assert capability_rule("app_groups").automation is CapabilityAutomation.API_ADDITIVE
    assert capability_rule("HEALTHKIT").automation is CapabilityAutomation.API_ADDITIVE
    assert capability_rule("INCREASED_MEMORY_LIMIT").automation is CapabilityAutomation.MANUAL
    assert capability_rule("KEYCHAIN_SHARING").automation is CapabilityAutomation.LOCAL_ONLY
    assert capability_rule("CLINICAL_HEALTH_RECORDS").automation is CapabilityAutomation.LOCAL_ONLY
    assert (
        capability_rule("HEALTHKIT_BACKGROUND_DELIVERY").automation
        is CapabilityAutomation.LOCAL_ONLY
    )
    assert capability_rule("UNREVIEWED").automation is CapabilityAutomation.BLOCKED


def test_requirements_plan_existing_api_local_manual_and_unknown_states() -> None:
    state = snapshot(capability("CAP_HEALTH", "HEALTHKIT"))
    requirements = tuple(
        capability_requirement(
            snapshot=state,
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type=value,
        )
        for value in (
            "HEALTHKIT",
            "APP_GROUPS",
            "INCREASED_MEMORY_LIMIT",
            "KEYCHAIN_SHARING",
            "UNREVIEWED",
        )
    )

    plan = plan_apple_resources(
        task_name="Example", snapshot_sha256="digest", requirements=requirements
    )
    dispositions = {value.target: value.disposition for value in plan.operations}

    assert dispositions == {
        "APP_GROUPS": OperationDisposition.SAFE_AUTOMATIC,
        "HEALTHKIT": OperationDisposition.NO_OP,
        "INCREASED_MEMORY_LIMIT": OperationDisposition.MANUAL_REQUIRED,
        "KEYCHAIN_SHARING": OperationDisposition.NO_OP,
        "UNREVIEWED": OperationDisposition.BLOCKED,
    }


def test_reuses_or_adds_and_reverifies_allowlisted_capability() -> None:
    existing = capability("CAP_HEALTH", "HEALTHKIT")
    reuse_gateway = FakeGateway([(existing,)], existing)
    assert (
        CapabilityReconciler(reuse_gateway).ensure(
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type="HEALTHKIT",
        )
        == existing
    )
    assert reuse_gateway.add_calls == []

    created = capability("CAP_GROUPS", "APP_GROUPS")
    add_gateway = FakeGateway([(), (created,)], created)
    assert (
        CapabilityReconciler(add_gateway).ensure(
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type="APP_GROUPS",
        )
        == created
    )
    assert add_gateway.add_calls == [("BUNDLE_ONE", "APP_GROUPS")]


def test_recovers_uncertain_add_but_never_calls_non_allowlisted_capability() -> None:
    created = capability("CAP_GROUPS", "APP_GROUPS")
    timeout = AdapterError(
        ErrorCode.ADAPTER_TIMEOUT,
        "timeout",
        adapter="asc",
        operation="capabilities-add",
    )
    gateway = FakeGateway([(), (created,)], timeout)
    assert (
        CapabilityReconciler(gateway).ensure(
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type="APP_GROUPS",
        )
        == created
    )
    assert len(gateway.add_calls) == 1

    for value in ("KEYCHAIN_SHARING", "INCREASED_MEMORY_LIMIT", "UNREVIEWED"):
        blocked_gateway = FakeGateway([], created)
        with pytest.raises(ConfigurationError):
            CapabilityReconciler(blocked_gateway).ensure(
                bundle_resource_id="BUNDLE_ONE",
                bundle_id="io.example.app",
                capability_type=value,
            )
        assert blocked_gateway.add_calls == []


def test_fails_for_duplicates_mismatched_create_or_missing_verification() -> None:
    first = capability("ONE", "HEALTHKIT")
    second = capability("TWO", "HEALTHKIT")
    with pytest.raises(AdapterError) as duplicate:
        CapabilityReconciler(FakeGateway([(first, second)], first)).ensure(
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type="HEALTHKIT",
        )
    assert duplicate.value.code is ErrorCode.APPLE_RESOURCE_CONFLICT

    wrong = capability("WRONG", "APP_GROUPS")
    wrong = AppleCapabilityState(
        wrong.resource_id, "OTHER_BUNDLE", wrong.capability_type, wrong.settings
    )
    with pytest.raises(AdapterError) as mismatched:
        CapabilityReconciler(FakeGateway([()], wrong)).ensure(
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type="APP_GROUPS",
        )
    assert mismatched.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID

    with pytest.raises(AdapterError) as missing:
        CapabilityReconciler(FakeGateway([(), ()], first)).ensure(
            bundle_resource_id="BUNDLE_ONE",
            bundle_id="io.example.app",
            capability_type="HEALTHKIT",
        )
    assert missing.value.operation == "capabilities-verify"


def test_asc_gateway_uses_only_list_and_add_commands() -> None:
    resource = {
        "type": "bundleIdCapabilities",
        "id": "CAP_HEALTH",
        "attributes": {"capabilityType": "HEALTHKIT", "settings": []},
    }
    client = RecordingClient([{"data": [resource]}, {"data": resource}])
    gateway = AscCapabilityGateway(client)

    assert gateway.list("BUNDLE_ONE")[0].resource_id == "CAP_HEALTH"
    assert gateway.add("BUNDLE_ONE", "HEALTHKIT").resource_id == "CAP_HEALTH"
    assert client.calls == [
        (
            ("bundle-ids", "capabilities", "list", "--bundle", "BUNDLE_ONE"),
            True,
        ),
        (
            (
                "bundle-ids",
                "capabilities",
                "add",
                "--bundle",
                "BUNDLE_ONE",
                "--capability",
                "HEALTHKIT",
            ),
            False,
        ),
    ]
