"""Tests for additive explicit Apple Bundle ID reconciliation."""

from __future__ import annotations

import pytest

from sideloadedipa.adapters.apple import (
    AscBundleIdGateway,
    AscResponse,
    BundleIdReconciler,
    bundle_id_requirement,
    exact_bundle_id_matches,
)
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleStateSnapshot,
    FrozenJsonObject,
    OperationDisposition,
    freeze_json,
)
from sideloadedipa.errors import AdapterError, ErrorCode


def bundle(resource_id: str, identifier: str) -> AppleBundleIdentifierState:
    return AppleBundleIdentifierState(
        resource_id=resource_id,
        identifier=identifier,
        name=identifier,
        platform="IOS",
    )


class FakeGateway:
    def __init__(
        self,
        listings: list[tuple[AppleBundleIdentifierState, ...]],
        *,
        create_result: AppleBundleIdentifierState | AdapterError,
    ) -> None:
        self.listings = listings
        self.create_result = create_result
        self.create_calls: list[tuple[str, str]] = []

    def list(self) -> tuple[AppleBundleIdentifierState, ...]:
        return self.listings.pop(0)

    def create(self, *, identifier: str, name: str) -> AppleBundleIdentifierState:
        self.create_calls.append((identifier, name))
        if isinstance(self.create_result, AdapterError):
            raise self.create_result
        return self.create_result


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
        frozen = freeze_json(self.documents.pop(0))
        assert isinstance(frozen, FrozenJsonObject)
        return AscResponse(frozen, ("asc", *args), 0.01)


def uncertain_error(code: ErrorCode = ErrorCode.ADAPTER_TIMEOUT) -> AdapterError:
    return AdapterError(code, "uncertain", adapter="asc", operation="bundle-ids-create")


def test_exact_lookup_is_case_insensitive_and_not_prefix_based() -> None:
    values = (
        bundle("ONE", "IO.Example.App"),
        bundle("CHILD", "io.example.app.child"),
    )

    assert exact_bundle_id_matches(values, "io.example.app") == (values[0],)


def test_requirement_uses_snapshot_and_creation_policy() -> None:
    snapshot = AppleStateSnapshot(
        snapshot_sha256="digest",
        bundle_ids=(bundle("ONE", "io.example.app"),),
        capabilities=(),
        certificates=(),
        devices=(),
        profiles=(),
    )

    existing = bundle_id_requirement(
        snapshot=snapshot,
        identifier="IO.EXAMPLE.APP",
        allow_creation=True,
    )
    missing = bundle_id_requirement(
        snapshot=snapshot,
        identifier="io.example.other",
        allow_creation=False,
    )

    assert existing.matching_resource_ids == ("ONE",)
    assert existing.missing_disposition is OperationDisposition.SAFE_AUTOMATIC
    assert missing.matching_resource_ids == ()
    assert missing.missing_disposition is OperationDisposition.MANUAL_REQUIRED


def test_reuses_existing_bundle_id_without_create() -> None:
    existing = bundle("ONE", "io.example.app")
    gateway = FakeGateway([(existing,)], create_result=existing)

    result = BundleIdReconciler(gateway).ensure(identifier="io.example.app", name="Example")

    assert result == existing
    assert gateway.create_calls == []


def test_creates_once_and_validates_returned_identifier() -> None:
    created = bundle("CREATED", "IO.Example.App")
    gateway = FakeGateway([()], create_result=created)

    result = BundleIdReconciler(gateway).ensure(identifier="io.example.app", name="Example")

    assert result == created
    assert gateway.create_calls == [("io.example.app", "Example")]


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.ADAPTER_TIMEOUT,
        ErrorCode.ADAPTER_UNAVAILABLE,
        ErrorCode.APPLE_API_FAILED,
        ErrorCode.APPLE_RESOURCE_CONFLICT,
    ],
)
def test_recovers_an_uncertain_create_by_exact_relookup(code: ErrorCode) -> None:
    recovered = bundle("RECOVERED", "io.example.app")
    gateway = FakeGateway([(), (recovered,)], create_result=uncertain_error(code))

    result = BundleIdReconciler(gateway).ensure(identifier="io.example.app", name="Example")

    assert result == recovered
    assert len(gateway.create_calls) == 1


def test_does_not_retry_when_uncertain_create_is_still_missing() -> None:
    error = uncertain_error()
    gateway = FakeGateway([(), ()], create_result=error)

    with pytest.raises(AdapterError) as caught:
        BundleIdReconciler(gateway).ensure(identifier="io.example.app", name="Example")

    assert caught.value is error
    assert len(gateway.create_calls) == 1


def test_fails_closed_for_duplicate_or_mismatched_resources() -> None:
    duplicate_gateway = FakeGateway(
        [(bundle("ONE", "io.example.app"), bundle("TWO", "IO.EXAMPLE.APP"))],
        create_result=bundle("UNUSED", "io.example.app"),
    )
    with pytest.raises(AdapterError) as duplicate:
        BundleIdReconciler(duplicate_gateway).ensure(identifier="io.example.app", name="Example")
    assert duplicate.value.code is ErrorCode.APPLE_RESOURCE_CONFLICT
    assert duplicate_gateway.create_calls == []

    mismatched_gateway = FakeGateway([()], create_result=bundle("WRONG", "io.example.other"))
    with pytest.raises(AdapterError) as mismatched:
        BundleIdReconciler(mismatched_gateway).ensure(identifier="io.example.app", name="Example")
    assert mismatched.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID


def test_does_not_relookup_after_a_certain_create_failure() -> None:
    error = uncertain_error(ErrorCode.APPLE_AUTHORIZATION_FAILED)
    gateway = FakeGateway([()], create_result=error)

    with pytest.raises(AdapterError) as caught:
        BundleIdReconciler(gateway).ensure(identifier="io.example.app", name="Example")

    assert caught.value is error
    assert gateway.listings == []


def test_asc_gateway_uses_only_list_and_create_commands() -> None:
    resource = {
        "type": "bundleIds",
        "id": "ONE",
        "attributes": {
            "identifier": "io.example.app",
            "name": "Example",
            "platform": "IOS",
        },
    }
    client = RecordingClient([{"data": [resource]}, {"data": resource}])
    gateway = AscBundleIdGateway(client)

    assert gateway.list()[0].resource_id == "ONE"
    assert gateway.create(identifier="io.example.app", name="Example").resource_id == "ONE"
    assert client.calls == [
        (("bundle-ids", "list"), True),
        (
            (
                "bundle-ids",
                "create",
                "--identifier",
                "io.example.app",
                "--name",
                "Example",
                "--platform",
                "IOS",
            ),
            False,
        ),
    ]
