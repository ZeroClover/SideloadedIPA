"""Production composition for the fail-closed signed-IPA verifier."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from sideloadedipa.domain import (
    ProvisioningProfile,
    SigningPlan,
    VerificationFinding,
    VerificationResult,
)
from sideloadedipa.verification.artifact import (
    SignedArtifactEntitlementEvidence,
    SignedEntitlementInspector,
    inspect_signed_entitlements,
)
from sideloadedipa.verification.integrity import verify_output_integrity
from sideloadedipa.verification.profiles import (
    EmbeddedProfileValidator,
    OpenSSLEmbeddedProfileValidator,
    verify_signed_profiles,
)
from sideloadedipa.verification.report import build_verification_result
from sideloadedipa.verification.signatures import verify_signed_signatures
from sideloadedipa.verification.three_way import verify_three_way_entitlements


class EntitlementEvidenceLoader(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        signed_ipa: Path,
        *,
        inspector: SignedEntitlementInspector | None = None,
    ) -> SignedArtifactEntitlementEvidence: ...


class EntitlementVerifier(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        profiles: tuple[ProvisioningProfile, ...],
        evidence: SignedArtifactEntitlementEvidence,
    ) -> tuple[VerificationFinding, ...]: ...


class ProfileVerifier(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        signed_ipa: Path,
        profiles: tuple[ProvisioningProfile, ...],
        *,
        validator: EmbeddedProfileValidator,
    ) -> tuple[VerificationFinding, ...]: ...


class SignedArtifactVerifier(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        signed_ipa: Path,
    ) -> tuple[VerificationFinding, ...]: ...


class OutputIntegrityVerifier(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        source_ipa: Path,
        signed_ipa: Path,
    ) -> tuple[VerificationFinding, ...]: ...


def _verify_entitlements(
    plan: SigningPlan,
    profiles: tuple[ProvisioningProfile, ...],
    evidence: SignedArtifactEntitlementEvidence,
) -> tuple[VerificationFinding, ...]:
    return verify_three_way_entitlements(plan, profiles, evidence)


@dataclass(frozen=True, slots=True)
class VerificationChecks:
    inspect_entitlements: EntitlementEvidenceLoader = inspect_signed_entitlements
    verify_entitlements: EntitlementVerifier = _verify_entitlements
    verify_profiles: ProfileVerifier = verify_signed_profiles
    verify_signatures: SignedArtifactVerifier = verify_signed_signatures
    verify_integrity: OutputIntegrityVerifier = verify_output_integrity


@dataclass(frozen=True, slots=True)
class PackageVerifier:
    """Run every required check and derive the sole publication-gate result."""

    source_ipa: Path
    profiles: tuple[ProvisioningProfile, ...]
    now: datetime
    refresh_threshold: timedelta = timedelta(days=30)
    entitlement_inspector: SignedEntitlementInspector | None = None
    profile_validator: EmbeddedProfileValidator | None = None
    checks: VerificationChecks = VerificationChecks()

    def verify(self, plan: SigningPlan, signed_ipa: Path) -> VerificationResult:
        validator = self.profile_validator or OpenSSLEmbeddedProfileValidator(
            now=self.now,
            refresh_threshold=self.refresh_threshold,
        )
        evidence = self.checks.inspect_entitlements(
            plan,
            signed_ipa,
            inspector=self.entitlement_inspector,
        )
        findings = (
            *self.checks.verify_entitlements(plan, self.profiles, evidence),
            *self.checks.verify_profiles(
                plan,
                signed_ipa,
                self.profiles,
                validator=validator,
            ),
            *self.checks.verify_signatures(plan, signed_ipa),
            *self.checks.verify_integrity(plan, self.source_ipa, signed_ipa),
        )
        artifact_sha256 = hashlib.sha256(signed_ipa.read_bytes()).hexdigest()
        return build_verification_result(plan, artifact_sha256, findings)
