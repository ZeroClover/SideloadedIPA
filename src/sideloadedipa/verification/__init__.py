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
from sideloadedipa.verification.integrity import verify_output_integrity
from sideloadedipa.verification.report import (
    VERIFICATION_REPORT_SCHEMA_VERSION,
    build_verification_result,
    canonical_verification_report_json,
    human_verification_report,
    required_verification_checks,
    verification_publication_gate,
    verification_report_sha256,
)
from sideloadedipa.verification.signatures import verify_signed_signatures
from sideloadedipa.verification.service import PackageVerifier, VerificationChecks
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
    "VERIFICATION_REPORT_SCHEMA_VERSION",
    "build_verification_result",
    "canonical_verification_report_json",
    "human_verification_report",
    "inspect_signed_entitlements",
    "PackageVerifier",
    "VerificationChecks",
    "verify_output_integrity",
    "verify_signed_signatures",
    "verify_three_way_entitlements",
    "required_verification_checks",
    "verification_publication_gate",
    "verification_report_sha256",
]
