"""Fail-closed verification building blocks."""

from sideloadedipa.verification.entitlements import (
    EntitlementComparison,
    EntitlementComparisonMode,
    EntitlementDifference,
    EntitlementIdentityContext,
    compare_entitlements,
)

__all__ = [
    "EntitlementComparison",
    "EntitlementComparisonMode",
    "EntitlementDifference",
    "EntitlementIdentityContext",
    "compare_entitlements",
]
