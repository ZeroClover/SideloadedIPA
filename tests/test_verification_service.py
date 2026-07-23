"""Tests for production fail-closed verifier composition."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

import sideloadedipa.verification.service as service_module
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

    def inspect(
        self,
        value: SigningPlan,
        signed_root: Path,
        artifact_sha256: str,
        *,
        inspector: object = None,
    ):
        del inspector
        assert signed_root.is_dir()
        self.calls.append("entitlement-evidence")
        return SignedArtifactEntitlementEvidence(
            value.plan_sha256,
            artifact_sha256,
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

    def profiles(self, value: SigningPlan, signed_root: Path, profiles, *, validator):
        del value, profiles, validator
        assert signed_root.is_dir()
        self.calls.append("profiles")
        return tuple(
            VerificationFinding(ROOT, check, True)
            for check in (
                "bundle-identifier",
                "embedded-profile-sha256",
                "embedded-profile-validation",
            )
        )

    def signatures(self, value: SigningPlan, signed_root: Path):
        del value
        assert signed_root.is_dir()
        self.calls.append("signatures")
        return (
            VerificationFinding(ROOT, "code-signature", not self.fail_signatures),
            VerificationFinding(ROOT, "nested-resource-seal", True),
        )

    def integrity(
        self,
        value: SigningPlan,
        source_root: Path,
        signed_root: Path,
        source_sha256: str,
        artifact_sha256: str,
    ):
        del value, source_sha256, artifact_sha256
        assert source_root.is_dir()
        assert signed_root.is_dir()
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


def _ipa(path: Path, marker: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/App.app/marker", marker)


def test_runs_every_independent_check_and_hashes_the_actual_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ipa"
    artifact = tmp_path / "signed.ipa"
    _ipa(source, b"source")
    _ipa(artifact, b"signed")
    checks = RecordingChecks()
    extractions: list[Path] = []
    original_extract = service_module.extract_ipa_safely

    def recording_extract(value: Path, destination: Path) -> None:
        extractions.append(value)
        original_extract(value, destination)

    monkeypatch.setattr(service_module, "extract_ipa_safely", recording_extract)

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
    assert extractions == [source, artifact]
    assert result.artifact_sha256 == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert result.passed is True
    assert len(result.findings) == 17


def test_failed_required_check_closes_the_publication_gate(tmp_path: Path) -> None:
    source = tmp_path / "source.ipa"
    artifact = tmp_path / "signed.ipa"
    _ipa(source, b"source")
    _ipa(artifact, b"signed")
    checks = RecordingChecks(fail_signatures=True)

    result = PackageVerifier(
        source,
        (profile(),),
        NOW,
        checks=checks.dependencies(),
    ).verify(plan(), artifact)

    assert result.passed is False
