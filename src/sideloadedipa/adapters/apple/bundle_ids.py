"""Additive, idempotent reconciliation for explicit Apple Bundle IDs."""

from __future__ import annotations

from typing import Protocol

from sideloadedipa.adapters.apple.state import (
    AscStateReader,
    collect_bundle_identifiers,
    decode_bundle_identifier_response,
)
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleResourceKind,
    AppleResourceRequirement,
    AppleStateSnapshot,
    OperationDisposition,
)
from sideloadedipa.errors import AdapterError, ErrorCode

_UNCERTAIN_CREATE_ERRORS = frozenset(
    {
        ErrorCode.ADAPTER_TIMEOUT,
        ErrorCode.ADAPTER_UNAVAILABLE,
        ErrorCode.APPLE_API_FAILED,
        ErrorCode.APPLE_RESOURCE_CONFLICT,
    }
)


class BundleIdGateway(Protocol):
    def list(self) -> tuple[AppleBundleIdentifierState, ...]: ...

    def create(self, *, identifier: str, name: str) -> AppleBundleIdentifierState: ...


class AscBundleIdGateway:
    def __init__(self, client: AscStateReader) -> None:
        self.client = client

    def list(self) -> tuple[AppleBundleIdentifierState, ...]:
        return collect_bundle_identifiers(self.client)

    def create(self, *, identifier: str, name: str) -> AppleBundleIdentifierState:
        response = self.client.run_json(
            (
                "bundle-ids",
                "create",
                "--identifier",
                identifier,
                "--name",
                name,
                "--platform",
                "IOS",
            )
        )
        return decode_bundle_identifier_response(response)


def exact_bundle_id_matches(
    bundle_ids: tuple[AppleBundleIdentifierState, ...], identifier: str
) -> tuple[AppleBundleIdentifierState, ...]:
    """Return complete identifier matches using Apple's case-insensitive semantics."""

    target = identifier.casefold()
    return tuple(
        sorted(
            (value for value in bundle_ids if value.identifier.casefold() == target),
            key=lambda value: value.resource_id,
        )
    )


def bundle_id_requirement(
    *,
    snapshot: AppleStateSnapshot,
    identifier: str,
    allow_creation: bool,
) -> AppleResourceRequirement:
    matches = exact_bundle_id_matches(snapshot.bundle_ids, identifier)
    return AppleResourceRequirement(
        resource_kind=AppleResourceKind.BUNDLE_ID,
        action="ensure-explicit-bundle-id",
        target=identifier,
        bundle_id=identifier,
        matching_resource_ids=tuple(value.resource_id for value in matches),
        missing_disposition=(
            OperationDisposition.SAFE_AUTOMATIC
            if allow_creation
            else OperationDisposition.MANUAL_REQUIRED
        ),
        remediation=(
            "allow additive App ID creation for this task"
            if allow_creation
            else "register this explicit App ID as an Account Holder or Admin"
        ),
    )


class BundleIdReconciler:
    def __init__(self, gateway: BundleIdGateway) -> None:
        self.gateway = gateway

    @staticmethod
    def _require_exact_one(
        matches: tuple[AppleBundleIdentifierState, ...], identifier: str
    ) -> AppleBundleIdentifierState | None:
        if len(matches) > 1:
            raise AdapterError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "multiple Apple Bundle IDs match the exact identifier",
                adapter="asc",
                operation="bundle-id-lookup",
                bundle_id=identifier,
                remediation="resolve the duplicate resources in the Apple Developer portal",
                safe_details=(("resource_ids", tuple(value.resource_id for value in matches)),),
            )
        return matches[0] if matches else None

    def ensure(
        self,
        *,
        identifier: str,
        name: str,
        bundle_ids: tuple[AppleBundleIdentifierState, ...] | None = None,
    ) -> AppleBundleIdentifierState:
        existing = self._require_exact_one(
            exact_bundle_id_matches(
                self.gateway.list() if bundle_ids is None else bundle_ids,
                identifier,
            ),
            identifier,
        )
        if existing is not None:
            return existing

        try:
            created = self.gateway.create(identifier=identifier, name=name)
        except AdapterError as error:
            if error.code not in _UNCERTAIN_CREATE_ERRORS:
                raise
            recovered = self._require_exact_one(
                exact_bundle_id_matches(self.gateway.list(), identifier), identifier
            )
            if recovered is not None:
                return recovered
            raise

        if created.identifier.casefold() != identifier.casefold():
            raise AdapterError(
                ErrorCode.ADAPTER_RESPONSE_INVALID,
                "created Apple Bundle ID does not match the requested identifier",
                adapter="asc",
                operation="bundle-ids-create",
                bundle_id=identifier,
                remediation="inspect the returned stable resource ID before retrying",
                safe_details=(
                    ("resource_id", created.resource_id),
                    ("returned_identifier", created.identifier),
                ),
            )
        return created
