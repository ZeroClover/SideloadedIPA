"""Capability classification policy independent of Apple API adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
