"""Idempotent provisioning-profile reuse and additive replacement."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Protocol

from sideloadedipa.adapters.apple.asc import AscResponse
from sideloadedipa.adapters.apple.state import AscStateReader, collect_profiles
from sideloadedipa.domain import (
    AppleProfileState,
    ProfileType,
    ProfileValidationRequest,
    ProvisioningProfile,
    thaw_json,
)
from sideloadedipa.errors import AdapterError, DomainError, ErrorCode

_UNCERTAIN_CREATE_ERRORS = frozenset(
    {
        ErrorCode.ADAPTER_TIMEOUT,
        ErrorCode.ADAPTER_UNAVAILABLE,
        ErrorCode.APPLE_API_FAILED,
        ErrorCode.APPLE_RESOURCE_CONFLICT,
    }
)
_STALE_PROFILE_ERRORS = frozenset(
    {
        ErrorCode.APPLE_PROFILE_DECODE_FAILED,
        ErrorCode.APPLE_PROFILE_INVALID,
        ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED,
    }
)


class ProfileGateway(Protocol):
    def list(self) -> tuple[AppleProfileState, ...]: ...

    def create(
        self,
        *,
        name: str,
        profile_type: ProfileType,
        bundle_resource_id: str,
        certificate_resource_id: str,
        device_resource_ids: tuple[str, ...],
    ) -> str: ...

    def download(self, resource_id: str) -> bytes: ...


class ProfileContentValidator(Protocol):
    def validate(
        self, content: bytes, request: ProfileValidationRequest
    ) -> ProvisioningProfile: ...


@dataclass(frozen=True, slots=True)
class ProfileSyncRequest:
    base_name: str
    bundle_resource_id: str
    certificate_resource_id: str
    device_resource_ids: tuple[str, ...]
    validation: ProfileValidationRequest


@dataclass(frozen=True, slots=True)
class ProfileReconciliationResult:
    profile: ProvisioningProfile
    content: bytes = field(repr=False)
    created: bool = False
    stale_resource_ids: tuple[str, ...] = ()
    state: AppleProfileState | None = field(default=None, repr=False)


def _invalid_response(message: str, field_name: str) -> AdapterError:
    return AdapterError(
        ErrorCode.ADAPTER_RESPONSE_INVALID,
        message,
        adapter="asc",
        operation="profiles",
        remediation="re-read profile state with the supported asc version",
        safe_details=(("field", field_name),),
    )


def _response_data(response: AscResponse, resource_id: str | None = None) -> Mapping[str, object]:
    if response.document is None:
        raise _invalid_response("profile response is empty", "data")
    document = thaw_json(response.document)
    if not isinstance(document, Mapping):
        raise _invalid_response("profile response root is not an object", "root")
    data = document.get("data")
    if not isinstance(data, Mapping):
        raise _invalid_response("profile response data is not an object", "data")
    if data.get("type") != "profiles":
        raise _invalid_response("profile response has the wrong resource type", "data.type")
    returned_id = data.get("id")
    if not isinstance(returned_id, str) or not returned_id:
        raise _invalid_response("profile response has no resource ID", "data.id")
    if resource_id is not None and returned_id != resource_id:
        raise _invalid_response("profile response ID differs from the requested ID", "data.id")
    return data


class AscProfileGateway:
    def __init__(self, client: AscStateReader) -> None:
        self.client = client

    def list(self) -> tuple[AppleProfileState, ...]:
        return collect_profiles(self.client)

    def create(
        self,
        *,
        name: str,
        profile_type: ProfileType,
        bundle_resource_id: str,
        certificate_resource_id: str,
        device_resource_ids: tuple[str, ...],
    ) -> str:
        response = self.client.run_json(
            (
                "profiles",
                "create",
                "--name",
                name,
                "--profile-type",
                profile_type.value,
                "--bundle",
                bundle_resource_id,
                "--certificate",
                certificate_resource_id,
                "--device",
                ",".join(device_resource_ids),
            )
        )
        data = _response_data(response)
        resource_id = data["id"]
        if not isinstance(resource_id, str):  # narrowed by _response_data
            raise AssertionError("unreachable profile resource ID type")
        return resource_id

    def download(self, resource_id: str) -> bytes:
        data = _response_data(
            self.client.run_json(("profiles", "view", "--id", resource_id)),
            resource_id,
        )
        attributes = data.get("attributes")
        if not isinstance(attributes, Mapping):
            raise _invalid_response("profile response has no attributes", "data.attributes")
        content = attributes.get("profileContent")
        if not isinstance(content, str) or not content:
            raise _invalid_response(
                "profile response has no downloadable content",
                "data.attributes.profileContent",
            )
        try:
            return base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as error:
            raise _invalid_response(
                "profile response content is not valid base64",
                "data.attributes.profileContent",
            ) from error


def next_profile_name(existing: tuple[AppleProfileState, ...], base_name: str) -> str:
    """Choose the first unoccupied numeric revision while preserving account naming style."""

    occupied = {profile.name.casefold() for profile in existing}
    if base_name.casefold() not in occupied:
        return base_name
    revision = 2
    while f"{base_name} {revision}".casefold() in occupied:
        revision += 1
    return f"{base_name} {revision}"


def _matches_relationships(profile: AppleProfileState, request: ProfileSyncRequest) -> bool:
    return (
        profile.bundle_resource_id == request.bundle_resource_id
        and profile.profile_type == request.validation.profile_type.value
        and profile.profile_state == "ACTIVE"
        and profile.certificate_resource_ids == (request.certificate_resource_id,)
        and profile.device_resource_ids == tuple(sorted(request.device_resource_ids))
    )


def _matches_create_intent(
    profile: AppleProfileState, name: str, request: ProfileSyncRequest
) -> bool:
    return profile.name == name and _matches_relationships(profile, request)


class ProfileReconciler:
    def __init__(self, gateway: ProfileGateway, validator: ProfileContentValidator) -> None:
        self.gateway = gateway
        self.validator = validator

    def _validated(
        self,
        state: AppleProfileState,
        request: ProfileSyncRequest,
    ) -> tuple[ProvisioningProfile, bytes]:
        content = self.gateway.download(state.resource_id)
        validation = replace(request.validation, resource_id=state.resource_id)
        profile = self.validator.validate(content, validation)
        if state.profile_sha256 is not None and state.profile_sha256 != profile.profile_sha256:
            raise DomainError(
                ErrorCode.APPLE_PROFILE_INVALID,
                "downloaded provisioning profile differs from the normalized Apple snapshot",
                bundle_id=request.validation.target_bundle_id,
                remediation="refresh Apple state before another profile reconciliation",
                safe_details=(("resource_id", state.resource_id),),
            )
        return profile, content

    @staticmethod
    def _recover_created(
        profiles: tuple[AppleProfileState, ...],
        name: str,
        request: ProfileSyncRequest,
    ) -> AppleProfileState | None:
        matches = tuple(
            profile for profile in profiles if _matches_create_intent(profile, name, request)
        )
        if len(matches) > 1:
            raise AdapterError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "multiple profiles match one attempted profile creation",
                adapter="asc",
                operation="profile-create-recovery",
                bundle_id=request.validation.target_bundle_id,
                remediation="inspect the matching profile resource IDs before another apply",
                safe_details=(("resource_ids", tuple(profile.resource_id for profile in matches)),),
            )
        return matches[0] if matches else None

    def ensure(
        self,
        request: ProfileSyncRequest,
        *,
        profiles: tuple[AppleProfileState, ...] | None = None,
    ) -> ProfileReconciliationResult:
        if not request.base_name or not request.device_resource_ids:
            raise DomainError(
                ErrorCode.DOMAIN_INVARIANT,
                "profile reconciliation requires a name and at least one enabled device",
                bundle_id=request.validation.target_bundle_id,
            )

        initial = self.gateway.list() if profiles is None else profiles
        relevant = tuple(
            profile
            for profile in initial
            if profile.bundle_resource_id == request.bundle_resource_id
        )
        validated: list[tuple[AppleProfileState, ProvisioningProfile, bytes]] = []
        for state in relevant:
            if not _matches_relationships(state, request):
                continue
            try:
                profile, content = self._validated(state, request)
                validated.append((state, profile, content))
            except (AdapterError, DomainError) as error:
                if error.code not in _STALE_PROFILE_ERRORS:
                    raise

        if validated:
            selected_state, profile, content = max(
                validated,
                key=lambda value: (
                    value[1].expires_at,
                    value[1].created_at,
                    value[1].resource_id,
                ),
            )
            return ProfileReconciliationResult(
                profile=profile,
                content=content,
                stale_resource_ids=tuple(
                    sorted(
                        state.resource_id
                        for state in relevant
                        if state.resource_id != profile.resource_id
                    )
                ),
                state=selected_state,
            )

        name = next_profile_name(initial, request.base_name)
        try:
            resource_id = self.gateway.create(
                name=name,
                profile_type=request.validation.profile_type,
                bundle_resource_id=request.bundle_resource_id,
                certificate_resource_id=request.certificate_resource_id,
                device_resource_ids=tuple(sorted(request.device_resource_ids)),
            )
        except AdapterError as error:
            if error.code not in _UNCERTAIN_CREATE_ERRORS:
                raise
            recovered = self._recover_created(self.gateway.list(), name, request)
            if recovered is None:
                raise
            created = recovered
        else:
            refreshed = self.gateway.list()
            created_candidate = next(
                (profile for profile in refreshed if profile.resource_id == resource_id),
                None,
            )
            if created_candidate is None or not _matches_create_intent(
                created_candidate, name, request
            ):
                raise AdapterError(
                    ErrorCode.ADAPTER_RESPONSE_INVALID,
                    "created profile was not present with the requested relationships",
                    adapter="asc",
                    operation="profile-create-verify",
                    bundle_id=request.validation.target_bundle_id,
                    remediation="re-run read-only planning before another apply attempt",
                    safe_details=(("resource_id", resource_id),),
                )
            created = created_candidate

        profile, content = self._validated(created, request)
        return ProfileReconciliationResult(
            profile=profile,
            content=content,
            created=True,
            stale_resource_ids=tuple(sorted(profile.resource_id for profile in relevant)),
            state=created,
        )
