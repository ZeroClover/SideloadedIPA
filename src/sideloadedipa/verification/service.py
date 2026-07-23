"""Production composition for the fail-closed signed-IPA verifier."""

from __future__ import annotations

import tempfile
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
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.util.atomics import file_sha256
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
        signed_root: Path,
        artifact_sha256: str,
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
        signed_root: Path,
        profiles: tuple[ProvisioningProfile, ...],
        *,
        validator: EmbeddedProfileValidator,
    ) -> tuple[VerificationFinding, ...]: ...


class SignedArtifactVerifier(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        signed_root: Path,
    ) -> tuple[VerificationFinding, ...]: ...


class OutputIntegrityVerifier(Protocol):
    def __call__(
        self,
        plan: SigningPlan,
        source_root: Path,
        output_root: Path,
        source_sha256: str,
        output_sha256: str,
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
        source_sha256 = file_sha256(self.source_ipa)
        artifact_sha256 = file_sha256(signed_ipa)
        with tempfile.TemporaryDirectory(prefix="sideloadedipa-verification-") as directory:
            root = Path(directory)
            source_root = root / "source"
            signed_root = root / "signed"
            extract_ipa_safely(self.source_ipa, source_root)
            extract_ipa_safely(signed_ipa, signed_root)
            evidence = self.checks.inspect_entitlements(
                plan,
                signed_root,
                artifact_sha256,
                inspector=self.entitlement_inspector,
            )
            findings = (
                *self.checks.verify_entitlements(plan, self.profiles, evidence),
                *self.checks.verify_profiles(
                    plan,
                    signed_root,
                    self.profiles,
                    validator=validator,
                ),
                *self.checks.verify_signatures(plan, signed_root),
                *self.checks.verify_integrity(
                    plan,
                    source_root,
                    signed_root,
                    source_sha256,
                    artifact_sha256,
                ),
            )
        return build_verification_result(plan, artifact_sha256, findings)
