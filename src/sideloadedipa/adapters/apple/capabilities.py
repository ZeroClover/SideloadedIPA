"""Allowlisted additive Apple capability planning and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sideloadedipa.adapters.apple.state import (
    AscStateReader,
    collect_capabilities,
    decode_capability_response,
)
from sideloadedipa.domain import (
    AppleCapabilityState,
    AppleResourceKind,
    AppleResourceRequirement,
    AppleStateSnapshot,
    OperationDisposition,
)
from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode


class CapabilityAutomation(StrEnum):
    API_ADDITIVE = "api-additive"
    MANUAL = "manual"
    LOCAL_ONLY = "local-only"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class CapabilityRule:
    capability_type: str
    automation: CapabilityAutomation
    remediation: str


CAPABILITY_REGISTRY: dict[str, CapabilityRule] = {
    "APP_GROUPS": CapabilityRule(
        "APP_GROUPS",
        CapabilityAutomation.API_ADDITIVE,
        "enable App Groups additively, then verify the configured group association",
    ),
    "HEALTHKIT": CapabilityRule(
        "HEALTHKIT",
        CapabilityAutomation.API_ADDITIVE,
        "enable HealthKit additively and re-read the App ID capability state",
    ),
    "INCREASED_MEMORY_LIMIT": CapabilityRule(
        "INCREASED_MEMORY_LIMIT",
        CapabilityAutomation.MANUAL,
        "have an Account Holder complete any required Apple approval and enable Increased Memory Limit",
    ),
    "KEYCHAIN_SHARING": CapabilityRule(
        "KEYCHAIN_SHARING",
        CapabilityAutomation.LOCAL_ONLY,
        "keep Keychain Sharing in the reviewed local entitlement policy; no separate Apple approval is required",
    ),
    "CLINICAL_HEALTH_RECORDS": CapabilityRule(
        "CLINICAL_HEALTH_RECORDS",
        CapabilityAutomation.LOCAL_ONLY,
        "express Clinical Health Records only in the reviewed HealthKit entitlement template",
    ),
    "HEALTHKIT_BACKGROUND_DELIVERY": CapabilityRule(
        "HEALTHKIT_BACKGROUND_DELIVERY",
        CapabilityAutomation.LOCAL_ONLY,
        "express HealthKit background delivery only in the reviewed HealthKit entitlement template",
    ),
}

_UNCERTAIN_ADD_ERRORS = frozenset(
    {
        ErrorCode.ADAPTER_TIMEOUT,
        ErrorCode.ADAPTER_UNAVAILABLE,
        ErrorCode.APPLE_API_FAILED,
        ErrorCode.APPLE_RESOURCE_CONFLICT,
    }
)


class CapabilityGateway(Protocol):
    def list(self, bundle_resource_id: str) -> tuple[AppleCapabilityState, ...]: ...

    def add(self, bundle_resource_id: str, capability_type: str) -> AppleCapabilityState: ...


class AscCapabilityGateway:
    def __init__(self, client: AscStateReader) -> None:
        self.client = client

    def list(self, bundle_resource_id: str) -> tuple[AppleCapabilityState, ...]:
        return collect_capabilities(self.client, bundle_resource_id)

    def add(self, bundle_resource_id: str, capability_type: str) -> AppleCapabilityState:
        response = self.client.run_json(
            (
                "bundle-ids",
                "capabilities",
                "add",
                "--bundle",
                bundle_resource_id,
                "--capability",
                capability_type,
            )
        )
        return decode_capability_response(response, bundle_resource_id)


def capability_rule(capability_type: str) -> CapabilityRule:
    normalized = capability_type.strip().upper()
    return CAPABILITY_REGISTRY.get(
        normalized,
        CapabilityRule(
            normalized,
            CapabilityAutomation.BLOCKED,
            "review this unsupported capability and extend the allowlist only from documented API evidence",
        ),
    )


def exact_capability_matches(
    capabilities: tuple[AppleCapabilityState, ...],
    bundle_resource_id: str,
    capability_type: str,
) -> tuple[AppleCapabilityState, ...]:
    normalized = capability_type.strip().upper()
    return tuple(
        sorted(
            (
                value
                for value in capabilities
                if value.bundle_resource_id == bundle_resource_id
                and value.capability_type == normalized
            ),
            key=lambda value: value.resource_id,
        )
    )


def capability_requirement(
    *,
    snapshot: AppleStateSnapshot,
    bundle_resource_id: str,
    bundle_id: str,
    capability_type: str,
) -> AppleResourceRequirement:
    rule = capability_rule(capability_type)
    matches = exact_capability_matches(
        snapshot.capabilities, bundle_resource_id, rule.capability_type
    )
    missing_disposition = {
        CapabilityAutomation.API_ADDITIVE: OperationDisposition.SAFE_AUTOMATIC,
        CapabilityAutomation.MANUAL: OperationDisposition.MANUAL_REQUIRED,
        CapabilityAutomation.LOCAL_ONLY: OperationDisposition.BLOCKED,
        CapabilityAutomation.BLOCKED: OperationDisposition.BLOCKED,
    }[rule.automation]
    return AppleResourceRequirement(
        resource_kind=AppleResourceKind.CAPABILITY,
        action="ensure-additive-capability",
        target=rule.capability_type,
        bundle_id=bundle_id,
        matching_resource_ids=tuple(value.resource_id for value in matches),
        missing_disposition=missing_disposition,
        remediation=rule.remediation,
        satisfied_without_resource=(
            rule.automation is CapabilityAutomation.LOCAL_ONLY and not matches
        ),
    )


class CapabilityReconciler:
    def __init__(self, gateway: CapabilityGateway) -> None:
        self.gateway = gateway

    @staticmethod
    def _require_exact_one(
        matches: tuple[AppleCapabilityState, ...],
        bundle_id: str,
        capability_type: str,
    ) -> AppleCapabilityState | None:
        if len(matches) > 1:
            raise AdapterError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "multiple capability resources match one App ID requirement",
                adapter="asc",
                operation="capability-lookup",
                bundle_id=bundle_id,
                remediation="resolve the duplicate capability resources before applying",
                safe_details=(
                    ("capability_type", capability_type),
                    ("resource_ids", tuple(value.resource_id for value in matches)),
                ),
            )
        return matches[0] if matches else None

    def ensure(
        self, *, bundle_resource_id: str, bundle_id: str, capability_type: str
    ) -> AppleCapabilityState:
        rule = capability_rule(capability_type)
        if rule.automation is not CapabilityAutomation.API_ADDITIVE:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "capability is outside the additive API allowlist",
                bundle_id=bundle_id,
                remediation=rule.remediation,
                safe_details=(("capability_type", rule.capability_type),),
            )

        def lookup() -> AppleCapabilityState | None:
            return self._require_exact_one(
                exact_capability_matches(
                    self.gateway.list(bundle_resource_id),
                    bundle_resource_id,
                    rule.capability_type,
                ),
                bundle_id,
                rule.capability_type,
            )

        existing = lookup()
        if existing is not None:
            return existing

        try:
            created = self.gateway.add(bundle_resource_id, rule.capability_type)
        except AdapterError as error:
            if error.code not in _UNCERTAIN_ADD_ERRORS:
                raise
            recovered = lookup()
            if recovered is not None:
                return recovered
            raise

        if (
            created.bundle_resource_id != bundle_resource_id
            or created.capability_type != rule.capability_type
        ):
            raise AdapterError(
                ErrorCode.ADAPTER_RESPONSE_INVALID,
                "created capability does not match the requested App ID capability",
                adapter="asc",
                operation="capabilities-add",
                bundle_id=bundle_id,
                remediation="inspect the returned stable resource ID before retrying",
                safe_details=(
                    ("resource_id", created.resource_id),
                    ("capability_type", created.capability_type),
                ),
            )

        verified = lookup()
        if verified is None:
            raise AdapterError(
                ErrorCode.ADAPTER_RESPONSE_INVALID,
                "new capability was not present in the verified App ID state",
                adapter="asc",
                operation="capabilities-verify",
                bundle_id=bundle_id,
                remediation="re-run read-only planning before another apply attempt",
                safe_details=(("capability_type", rule.capability_type),),
            )
        return verified
