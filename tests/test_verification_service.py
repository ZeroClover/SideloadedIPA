"""Tests for production fail-closed verifier composition."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    BundleNodeKind,
    ProfileType,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
)
from sideloadedipa.verification import SignedArtifactEntitlementEvidence
from sideloadedipa.verification.service import PackageVerifier, VerificationChecks

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
ROOT = PurePosixPath("Payload/App.app")


def plan() -> SigningPlan:
    node = SigningNodePlan(
        ROOT,
        ROOT / "App",
        BundleNodeKind.APP,
        0,
        "com.example.app",
        "PROFILE",
        PurePosixPath("profile.mobileprovision"),
        "6" * 64,
        (),
        "7" * 64,
    )
    return SigningPlan(
        "Example",
        "1" * 64,
        "2" * 64,
        "3" * 64,
        SigningBackendIdentity("fixture", "1", "4" * 64, "1"),
        (node,),
        "5" * 64,
    )


def profile() -> ProvisioningProfile:
    return ProvisioningProfile(
        "PROFILE",
        "Example Dev",
        ProfileType.IOS_APP_DEVELOPMENT,
        "com.example.app",
        "PREFIX.com.example.app",
        "TEAM",
        "3" * 64,
        ("device",),
        NOW,
        NOW,
        "6" * 64,
        PurePosixPath("profile.mobileprovision"),
        (),
    )


@dataclass
class RecordingChecks:
    calls: list[str] = field(default_factory=list)
    fail_signatures: bool = False

    def inspect(self, value: SigningPlan, artifact: Path, *, inspector: object = None):
        del inspector
        self.calls.append("entitlement-evidence")
        return SignedArtifactEntitlementEvidence(
            value.plan_sha256,
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
            (),
        )

    def entitlements(self, value: SigningPlan, profiles, evidence):
        del value, profiles, evidence
        self.calls.append("entitlements")
        return tuple(
            VerificationFinding(ROOT, check, True)
            for check in (
                "profile-entitlement-authorization",
                "signed-entitlements:arm64:xml",
                "signed-entitlements:arm64:der",
                "xml-der-entitlements:arm64",
            )
        )

    def profiles(self, value: SigningPlan, artifact: Path, profiles, *, validator):
        del value, artifact, profiles, validator
        self.calls.append("profiles")
        return tuple(
            VerificationFinding(ROOT, check, True)
            for check in (
                "bundle-identifier",
                "embedded-profile-sha256",
                "embedded-profile-validation",
            )
        )

    def signatures(self, value: SigningPlan, artifact: Path):
        del value, artifact
        self.calls.append("signatures")
        return (
            VerificationFinding(ROOT, "code-signature", not self.fail_signatures),
            VerificationFinding(ROOT, "nested-resource-seal", True),
        )

    def integrity(self, value: SigningPlan, source: Path, artifact: Path):
        del value, source, artifact
        self.calls.append("integrity")
        return tuple(
            VerificationFinding(ROOT, check, True)
            for check in (
                "source-artifact",
                "safe-output-archive",
                "source-plan-node-set",
                "output-graph-parity",
                "planned-identifiers",
                "executable-set",
                "protected-info-plists",
                "protected-payload",
            )
        )

    def dependencies(self) -> VerificationChecks:
        return VerificationChecks(
            self.inspect,
            self.entitlements,
            self.profiles,
            self.signatures,
            self.integrity,
        )


def test_runs_every_independent_check_and_hashes_the_actual_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.ipa"
    artifact = tmp_path / "signed.ipa"
    source.write_bytes(b"source")
    artifact.write_bytes(b"signed")
    checks = RecordingChecks()

    result = PackageVerifier(
        source,
        (profile(),),
        NOW,
        checks=checks.dependencies(),
    ).verify(plan(), artifact)

    assert checks.calls == [
        "entitlement-evidence",
        "entitlements",
        "profiles",
        "signatures",
        "integrity",
    ]
    assert result.artifact_sha256 == hashlib.sha256(b"signed").hexdigest()
    assert result.passed is True
    assert len(result.findings) == 17


def test_failed_required_check_closes_the_publication_gate(tmp_path: Path) -> None:
    source = tmp_path / "source.ipa"
    artifact = tmp_path / "signed.ipa"
    source.write_bytes(b"source")
    artifact.write_bytes(b"signed")
    checks = RecordingChecks(fail_signatures=True)

    result = PackageVerifier(
        source,
        (profile(),),
        NOW,
        checks=checks.dependencies(),
    ).verify(plan(), artifact)

    assert result.passed is False
