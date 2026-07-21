"""Fail-closed verification building blocks."""

from sideloadedipa.verification.artifact import (
    EntitlementRepresentationEvidence,
    SignedArtifactEntitlementEvidence,
    SignedEntitlementSliceEvidence,
    SignedNodeEntitlementEvidence,
    inspect_signed_entitlements,
)
from sideloadedipa.verification.entitlements import (
    EntitlementComparison,
    EntitlementComparisonMode,
    EntitlementDifference,
    EntitlementIdentityContext,
    compare_entitlements,
)
from sideloadedipa.verification.signatures import verify_signed_signatures
from sideloadedipa.verification.three_way import verify_three_way_entitlements

__all__ = [
    "EntitlementRepresentationEvidence",
    "EntitlementComparison",
    "EntitlementComparisonMode",
    "EntitlementDifference",
    "EntitlementIdentityContext",
    "SignedArtifactEntitlementEvidence",
    "SignedEntitlementSliceEvidence",
    "SignedNodeEntitlementEvidence",
    "compare_entitlements",
    "inspect_signed_entitlements",
    "verify_signed_signatures",
    "verify_three_way_entitlements",
]
