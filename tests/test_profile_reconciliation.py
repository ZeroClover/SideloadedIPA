"""Tests for idempotent provisioning-profile reuse and additive replacement."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import pytest

from sideloadedipa.adapters.apple import (
    AscProfileGateway,
    AscResponse,
    ProfileReconciler,
    ProfileSyncRequest,
    next_profile_name,
)
from sideloadedipa.domain import (
    AppleProfileState,
    FrozenJsonObject,
    ProfileType,
    ProfileValidationRequest,
    ProvisioningProfile,
    freeze_json,
)
from sideloadedipa.errors import AdapterError, DomainError, ErrorCode

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
BUNDLE_ID = "io.example.app"
BUNDLE_RESOURCE_ID = "BUNDLE_ONE"
CERTIFICATE_RESOURCE_ID = "CERTIFICATE_ONE"
DEVICE_RESOURCE_IDS = ("DEVICE_ONE", "DEVICE_TWO")


def state(
    resource_id: str,
    name: str,
    content: bytes,
    *,
    profile_state: str = "ACTIVE",
    certificate_ids: tuple[str, ...] = (CERTIFICATE_RESOURCE_ID,),
    device_ids: tuple[str, ...] = DEVICE_RESOURCE_IDS,
    bundle_resource_id: str = BUNDLE_RESOURCE_ID,
) -> AppleProfileState:
    return AppleProfileState(
        resource_id=resource_id,
        name=name,
        platform="IOS",
        profile_type=ProfileType.IOS_APP_DEVELOPMENT.value,
        profile_state=profile_state,
        uuid=f"UUID-{resource_id}",
        created_date="2026-07-20T00:00:00Z",
        expiration_date="2026-10-20T00:00:00Z",
        profile_sha256=hashlib.sha256(content).hexdigest(),
        bundle_resource_id=bundle_resource_id,
        certificate_resource_ids=certificate_ids,
        device_resource_ids=device_ids,
        profile_content=content,
    )


def sync_request(base_name: str = "LiveContainer Dev") -> ProfileSyncRequest:
    validation = ProfileValidationRequest(
        resource_id="",
        target_bundle_id=BUNDLE_ID,
        application_identifier=f"TEAM.{BUNDLE_ID}",
        team_id="TEAM",
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        certificate_sha256="certificate-sha256",
        device_udid_sha256=("device-one-sha256", "device-two-sha256"),
        path=PurePosixPath("LiveContainer/profile.mobileprovision"),
        expected_entitlements=(),
    )
    return ProfileSyncRequest(
        base_name=base_name,
        bundle_resource_id=BUNDLE_RESOURCE_ID,
        certificate_resource_id=CERTIFICATE_RESOURCE_ID,
        device_resource_ids=DEVICE_RESOURCE_IDS,
        validation=validation,
    )


class FakeGateway:
    def __init__(
        self,
        profiles: tuple[AppleProfileState, ...],
        contents: dict[str, bytes],
        *,
        create_error: AdapterError | None = None,
    ) -> None:
        self.profiles = list(profiles)
        self.contents = contents
        self.create_error = create_error
        self.create_calls: list[dict[str, object]] = []
        self.view_calls: list[str] = []
        self.list_calls = 0

    def list(self) -> tuple[AppleProfileState, ...]:
        self.list_calls += 1
        return tuple(self.profiles)

    def create(
        self,
        *,
        name: str,
        profile_type: ProfileType,
        bundle_resource_id: str,
        certificate_resource_id: str,
        device_resource_ids: tuple[str, ...],
    ) -> str:
        self.create_calls.append(
            {
                "name": name,
                "profile_type": profile_type,
                "bundle_resource_id": bundle_resource_id,
                "certificate_resource_id": certificate_resource_id,
                "device_resource_ids": device_resource_ids,
            }
        )
        resource_id = "PROFILE_NEW"
        content = b"valid-new"
        self.profiles.append(state(resource_id, name, content))
        self.contents[resource_id] = content
        if self.create_error is not None:
            raise self.create_error
        return resource_id

    def view(self, resource_id: str) -> AppleProfileState:
        self.view_calls.append(resource_id)
        return next(profile for profile in self.profiles if profile.resource_id == resource_id)


class FakeValidator:
    def validate(self, content: bytes, request: ProfileValidationRequest) -> ProvisioningProfile:
        if content.startswith(b"invalid"):
            raise DomainError(
                ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED,
                "fixture profile is stale",
                bundle_id=request.target_bundle_id,
            )
        expiry_days = 120 if content.endswith(b"newest") else 90
        return ProvisioningProfile(
            resource_id=request.resource_id,
            name=f"Validated {request.resource_id}",
            profile_type=request.profile_type,
            bundle_id=request.target_bundle_id,
            application_identifier=request.application_identifier,
            team_id=request.team_id,
            certificate_sha256=request.certificate_sha256,
            device_ids=request.device_udid_sha256,
            created_at=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=expiry_days),
            profile_sha256=hashlib.sha256(content).hexdigest(),
            path=request.path,
            entitlements=request.expected_entitlements,
        )


def uncertain_error() -> AdapterError:
    return AdapterError(
        ErrorCode.ADAPTER_TIMEOUT,
        "fixture timeout",
        adapter="asc",
        operation="profiles-create",
    )


def test_reuses_best_fully_valid_profile_without_creating_or_deleting() -> None:
    older = state("PROFILE_OLD", "LiveContainer Dev", b"valid-old")
    newest = state("PROFILE_NEWEST", "LiveContainer Dev 2", b"valid-newest")
    wrong_certificate = state(
        "PROFILE_WRONG_CERT",
        "LiveContainer Dev 3",
        b"valid-wrong-cert",
        certificate_ids=("CERTIFICATE_OTHER",),
    )
    gateway = FakeGateway(
        (older, newest, wrong_certificate),
        {
            older.resource_id: b"valid-old",
            newest.resource_id: b"valid-newest",
            wrong_certificate.resource_id: b"valid-wrong-cert",
        },
    )

    result = ProfileReconciler(gateway, FakeValidator()).ensure(sync_request())

    assert result.profile.resource_id == "PROFILE_NEWEST"
    assert result.content == b"valid-newest"
    assert result.created is False
    assert result.stale_resource_ids == ("PROFILE_OLD", "PROFILE_WRONG_CERT")
    assert gateway.create_calls == []
    assert not hasattr(gateway, "download")
    assert not hasattr(gateway, "delete")


def test_reuses_supplied_profile_snapshot_without_relisting_account() -> None:
    existing = state("PROFILE_EXISTING", "LiveContainer Dev", b"valid-existing")
    gateway = FakeGateway(
        (existing,),
        {existing.resource_id: b"valid-existing"},
    )

    result = ProfileReconciler(gateway, FakeValidator()).ensure(
        sync_request(),
        profiles=(existing,),
    )

    assert result.profile.resource_id == existing.resource_id
    assert result.state == existing
    assert gateway.list_calls == 0
    assert not hasattr(gateway, "download")


def test_successful_profile_creation_uses_one_targeted_view_without_relisting() -> None:
    gateway = FakeGateway((), {})

    result = ProfileReconciler(gateway, FakeValidator()).ensure(
        sync_request(),
        profiles=(),
    )

    assert result.created is True
    assert result.state is not None
    assert result.state.resource_id == "PROFILE_NEW"
    assert gateway.list_calls == 0
    assert gateway.view_calls == ["PROFILE_NEW"]


def test_creates_next_standard_name_after_all_existing_profiles_are_stale() -> None:
    first = state("PROFILE_ONE", "LiveContainer Dev", b"invalid-entitlements")
    second = state(
        "PROFILE_TWO",
        "LiveContainer Dev 2",
        b"valid-but-invalid-state",
        profile_state="INVALID",
    )
    gateway = FakeGateway(
        (first, second),
        {
            first.resource_id: b"invalid-entitlements",
            second.resource_id: b"valid-but-invalid-state",
        },
    )

    result = ProfileReconciler(gateway, FakeValidator()).ensure(sync_request())

    assert result.created is True
    assert result.profile.resource_id == "PROFILE_NEW"
    assert result.stale_resource_ids == ("PROFILE_ONE", "PROFILE_TWO")
    assert gateway.create_calls == [
        {
            "name": "LiveContainer Dev 3",
            "profile_type": ProfileType.IOS_APP_DEVELOPMENT,
            "bundle_resource_id": BUNDLE_RESOURCE_ID,
            "certificate_resource_id": CERTIFICATE_RESOURCE_ID,
            "device_resource_ids": DEVICE_RESOURCE_IDS,
        }
    ]


def test_recovers_an_accepted_create_after_an_uncertain_response() -> None:
    gateway = FakeGateway((), {}, create_error=uncertain_error())

    result = ProfileReconciler(gateway, FakeValidator()).ensure(
        sync_request(),
        profiles=(),
    )

    assert result.created is True
    assert result.profile.resource_id == "PROFILE_NEW"
    assert result.state is not None
    assert result.state.resource_id == "PROFILE_NEW"
    assert len(gateway.create_calls) == 1
    assert gateway.list_calls == 1
    assert gateway.view_calls == []


def test_blocks_ambiguous_uncertain_create_recovery() -> None:
    class DuplicateRecoveryGateway(FakeGateway):
        def create(self, **kwargs: object) -> str:
            name = kwargs["name"]
            assert isinstance(name, str)
            self.create_calls.append(dict(kwargs))
            self.profiles.extend(
                (
                    state("PROFILE_RECOVERED_ONE", name, b"valid-one"),
                    state("PROFILE_RECOVERED_TWO", name, b"valid-two"),
                )
            )
            raise uncertain_error()

    gateway = DuplicateRecoveryGateway((), {})

    with pytest.raises(AdapterError) as caught:
        ProfileReconciler(gateway, FakeValidator()).ensure(sync_request())

    assert caught.value.code is ErrorCode.APPLE_RESOURCE_CONFLICT
    assert dict(caught.value.safe_details)["resource_ids"] == (
        "PROFILE_RECOVERED_ONE",
        "PROFILE_RECOVERED_TWO",
    )


def test_does_not_retry_or_hide_an_unrecovered_create_failure() -> None:
    class FailingGateway(FakeGateway):
        def create(self, **kwargs: object) -> str:
            self.create_calls.append(dict(kwargs))
            raise uncertain_error()

    gateway = FailingGateway((), {})

    with pytest.raises(AdapterError) as caught:
        ProfileReconciler(gateway, FakeValidator()).ensure(sync_request())

    assert caught.value.code is ErrorCode.ADAPTER_TIMEOUT
    assert len(gateway.create_calls) == 1


@pytest.mark.parametrize("held_content", [None, b"different-held-content"])
def test_treats_invalid_held_content_as_stale_without_downloading(
    held_content: bytes | None,
) -> None:
    mismatched = replace(
        state("PROFILE_OLD", "LiveContainer Dev", b"snapshot-content"),
        profile_content=held_content,
    )
    mismatch_gateway = FakeGateway(
        (mismatched,),
        {},
    )

    replacement = ProfileReconciler(mismatch_gateway, FakeValidator()).ensure(sync_request())

    assert replacement.created is True
    assert replacement.stale_resource_ids == ("PROFILE_OLD",)
    assert not hasattr(mismatch_gateway, "download")


def test_treats_validator_digest_mismatch_as_stale() -> None:
    existing = state("PROFILE_OLD", "LiveContainer Dev", b"snapshot-content")
    gateway = FakeGateway((existing,), {})

    class DigestMismatchingValidator(FakeValidator):
        def validate(
            self,
            content: bytes,
            request: ProfileValidationRequest,
        ) -> ProvisioningProfile:
            profile = super().validate(content, request)
            if content == b"snapshot-content":
                return replace(profile, profile_sha256="0" * 64)
            return profile

    replacement = ProfileReconciler(gateway, DigestMismatchingValidator()).ensure(sync_request())

    assert replacement.created is True
    assert replacement.stale_resource_ids == ("PROFILE_OLD",)


def test_propagates_certain_create_failure_without_relookup() -> None:
    class UnauthorizedGateway(FakeGateway):
        def create(self, **kwargs: object) -> str:
            raise AdapterError(
                ErrorCode.APPLE_AUTHORIZATION_FAILED,
                "fixture role failure",
                adapter="asc",
                operation="profiles-create",
            )

    gateway = UnauthorizedGateway((), {})

    with pytest.raises(AdapterError) as caught:
        ProfileReconciler(gateway, FakeValidator()).ensure(sync_request())

    assert caught.value.code is ErrorCode.APPLE_AUTHORIZATION_FAILED
    assert gateway.list_calls == 1


def test_blocks_missing_devices_and_mismatched_created_state() -> None:
    empty_devices = sync_request()
    empty_devices = ProfileSyncRequest(
        base_name=empty_devices.base_name,
        bundle_resource_id=empty_devices.bundle_resource_id,
        certificate_resource_id=empty_devices.certificate_resource_id,
        device_resource_ids=(),
        validation=empty_devices.validation,
    )
    with pytest.raises(DomainError) as invariant:
        ProfileReconciler(FakeGateway((), {}), FakeValidator()).ensure(empty_devices)
    assert invariant.value.code is ErrorCode.DOMAIN_INVARIANT

    class MismatchedGateway(FakeGateway):
        def create(
            self,
            *,
            name: str,
            profile_type: ProfileType,
            bundle_resource_id: str,
            certificate_resource_id: str,
            device_resource_ids: tuple[str, ...],
        ) -> str:
            resource_id = super().create(
                name=name,
                profile_type=profile_type,
                bundle_resource_id=bundle_resource_id,
                certificate_resource_id=certificate_resource_id,
                device_resource_ids=device_resource_ids,
            )
            self.profiles[-1] = state(
                resource_id,
                "Wrong Name",
                self.contents[resource_id],
            )
            return resource_id

    with pytest.raises(AdapterError) as mismatch:
        ProfileReconciler(MismatchedGateway((), {}), FakeValidator()).ensure(sync_request())
    assert mismatch.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID


def test_next_profile_name_considers_all_profile_names_case_insensitively() -> None:
    profiles = (
        state("ONE", "LiveContainer Dev", b"one", bundle_resource_id="OTHER"),
        state("TWO", "livecontainer dev 2", b"two", bundle_resource_id="OTHER"),
    )

    assert next_profile_name(profiles, "LiveContainer Dev") == "LiveContainer Dev 3"
    assert next_profile_name(profiles, "Another App Dev") == "Another App Dev"


class GatewayClient:
    def __init__(self, content: str = "cHJvZmlsZQ==") -> None:
        self.content = content
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def run_json(
        self,
        args: tuple[str, ...],
        *,
        paginate: bool = False,
        allow_empty: bool = False,
    ) -> AscResponse:
        self.calls.append((args, paginate))
        document: dict[str, object]
        if args[:2] == ("profiles", "create"):
            document = {"data": {"type": "profiles", "id": "PROFILE_CREATED"}}
        elif "--include" in args:
            document = {
                "data": {
                    "type": "profiles",
                    "id": args[args.index("--id") + 1],
                    "attributes": {
                        "name": "LiveContainer Dev 3",
                        "platform": "IOS",
                        "profileType": "IOS_APP_DEVELOPMENT",
                        "profileState": "ACTIVE",
                        "profileContent": self.content,
                        "uuid": "UUID-PROFILE-CREATED",
                        "createdDate": "2026-07-21T00:00:00Z",
                        "expirationDate": "2027-07-21T00:00:00Z",
                    },
                    "relationships": {
                        "bundleId": {
                            "data": {
                                "type": "bundleIds",
                                "id": BUNDLE_RESOURCE_ID,
                            }
                        },
                        "certificates": {
                            "data": [
                                {
                                    "type": "certificates",
                                    "id": CERTIFICATE_RESOURCE_ID,
                                }
                            ]
                        },
                        "devices": {
                            "data": [
                                {"type": "devices", "id": value} for value in DEVICE_RESOURCE_IDS
                            ]
                        },
                    },
                }
            }
        else:
            raise AssertionError(f"unexpected ASC command: {args}")
        frozen = freeze_json(document)
        assert isinstance(frozen, FrozenJsonObject)
        return AscResponse(frozen, ("asc", *args), 0.01)


def test_asc_gateway_uses_exact_create_and_included_view_contract() -> None:
    client = GatewayClient()
    gateway = AscProfileGateway(client)

    resource_id = gateway.create(
        name="LiveContainer Dev 3",
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        bundle_resource_id=BUNDLE_RESOURCE_ID,
        certificate_resource_id=CERTIFICATE_RESOURCE_ID,
        device_resource_ids=DEVICE_RESOURCE_IDS,
    )
    state_value = gateway.view(resource_id)

    assert resource_id == "PROFILE_CREATED"
    assert state_value.resource_id == resource_id
    assert state_value.bundle_resource_id == BUNDLE_RESOURCE_ID
    assert state_value.profile_content == b"profile"
    assert client.calls == [
        (
            (
                "profiles",
                "create",
                "--name",
                "LiveContainer Dev 3",
                "--profile-type",
                "IOS_APP_DEVELOPMENT",
                "--bundle",
                BUNDLE_RESOURCE_ID,
                "--certificate",
                CERTIFICATE_RESOURCE_ID,
                "--device",
                ",".join(DEVICE_RESOURCE_IDS),
            ),
            False,
        ),
        (
            (
                "profiles",
                "view",
                "--id",
                "PROFILE_CREATED",
                "--include",
                "bundleId,certificates,devices",
            ),
            False,
        ),
    ]

    invalid_gateway = AscProfileGateway(GatewayClient("not-base64"))
    with pytest.raises(AdapterError) as invalid:
        invalid_gateway.view("PROFILE_CREATED")
    assert invalid.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID
    assert "not-base64" not in str(invalid.value.safe_details)


@pytest.mark.parametrize(
    "document",
    [
        None,
        {"data": []},
        {"data": {"type": "devices", "id": "PROFILE_CREATED"}},
        {"data": {"type": "profiles", "id": ""}},
        {"data": {"type": "profiles", "id": "PROFILE_OTHER", "attributes": {}}},
        {"data": {"type": "profiles", "id": "PROFILE_CREATED", "attributes": {}}},
    ],
)
def test_asc_gateway_rejects_malformed_included_view_responses(
    document: dict[str, object] | None,
) -> None:
    class StaticClient:
        def run_json(
            self,
            args: tuple[str, ...],
            *,
            paginate: bool = False,
            allow_empty: bool = False,
        ) -> AscResponse:
            frozen = freeze_json(document) if document is not None else None
            assert frozen is None or isinstance(frozen, FrozenJsonObject)
            return AscResponse(frozen, ("asc", *args), 0.01)

    with pytest.raises(AdapterError) as caught:
        AscProfileGateway(StaticClient()).view("PROFILE_CREATED")

    assert caught.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID


def test_reconciliation_result_repr_does_not_expose_profile_content() -> None:
    gateway = FakeGateway((), {})
    result = ProfileReconciler(gateway, FakeValidator()).ensure(sync_request())
    state_value = state("PROFILE_REPR", "LiveContainer Dev", b"valid-repr")

    assert base64.b64encode(result.content).decode() not in repr(result)
    assert "valid-new" not in repr(result)
    assert "valid-repr" not in repr(state_value)
