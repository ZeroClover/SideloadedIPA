"""Tests proving cache hits cannot bypass current verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.cache_decisions import TaskCacheRecord
from sideloadedipa.cache_reuse import CachePrerequisiteState, revalidate_cached_artifact
from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    ProfileType,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
    VerificationResult,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.verification import build_verification_result, required_verification_checks

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
ROOT = PurePosixPath("Payload/App.app")


def plan() -> SigningPlan:
    entitlements = normalize_entitlements({"application-identifier": "TEAM.io.example.app"})
    return SigningPlan(
        "Example",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        SigningBackendIdentity("fixture", "1", "d" * 64, "1"),
        (
            SigningNodePlan(
                ROOT,
                ROOT / "App",
                BundleNodeKind.APP,
                0,
                "io.example.app",
                "PROFILE",
                PurePosixPath("Example/profile.mobileprovision"),
                "e" * 64,
                entitlements.values,
                entitlements.sha256,
            ),
        ),
        "f" * 64,
    )


def profile() -> ProvisioningProfile:
    entitlements = normalize_entitlements({"application-identifier": "TEAM.io.example.app"})
    return ProvisioningProfile(
        "PROFILE",
        "Example Dev",
        ProfileType.IOS_APP_DEVELOPMENT,
        "io.example.app",
        "TEAM.io.example.app",
        "TEAM",
        "c" * 64,
        ("device",),
        NOW - timedelta(days=1),
        NOW + timedelta(days=90),
        "e" * 64,
        PurePosixPath("Example/profile.mobileprovision"),
        entitlements.values,
    )


def cache_record(artifact: Path) -> TaskCacheRecord:
    return TaskCacheRecord(
        "Example",
        1,
        "0" * 64,
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "1" * 64,
    )


def passing_findings(signing_plan: SigningPlan) -> tuple[VerificationFinding, ...]:
    return tuple(
        VerificationFinding(path, check.replace("*", "arm64"), True)
        for path, check in required_verification_checks(signing_plan)
    )


@dataclass
class RecordingVerifier:
    called: bool = False
    fail: bool = False

    def verify(self, signing_plan: SigningPlan, signed_ipa: Path) -> VerificationResult:
        self.called = True
        findings = passing_findings(signing_plan)
        if self.fail:
            findings = (*findings, VerificationFinding(ROOT, "oracle", False))
        return build_verification_result(
            signing_plan,
            hashlib.sha256(signed_ipa.read_bytes()).hexdigest(),
            findings,
        )


def ready() -> CachePrerequisiteState:
    return CachePrerequisiteState(True, "2" * 64)


def test_cache_hit_reopens_artifact_through_full_verifier(tmp_path: Path) -> None:
    artifact = tmp_path / "cached.ipa"
    artifact.write_bytes(b"cached signed ipa")
    verifier = RecordingVerifier()

    result = revalidate_cached_artifact(
        plan=plan(),
        cache_record=cache_record(artifact),
        artifact=artifact,
        prerequisites=ready(),
        profiles=(profile(),),
        now=NOW,
        refresh_threshold=timedelta(days=30),
        verifier=verifier,
    )

    assert verifier.called
    assert result.passed


@pytest.mark.parametrize("failure", ["prerequisite", "profile", "artifact"])
def test_current_preconditions_block_before_verifier(tmp_path: Path, failure: str) -> None:
    artifact = tmp_path / "cached.ipa"
    artifact.write_bytes(b"cached signed ipa")
    record = cache_record(artifact)
    prerequisite = ready()
    current_profile = profile()
    if failure == "prerequisite":
        prerequisite = CachePrerequisiteState(
            False,
            "2" * 64,
            (
                Diagnostic(
                    "apple.manual_required",
                    DiagnosticSeverity.ERROR,
                    "manual prerequisite missing",
                ),
            ),
        )
    elif failure == "profile":
        current_profile = replace(current_profile, expires_at=NOW + timedelta(days=7))
    else:
        artifact.write_bytes(b"tampered")
    verifier = RecordingVerifier()

    with pytest.raises(DomainError) as caught:
        revalidate_cached_artifact(
            plan=plan(),
            cache_record=record,
            artifact=artifact,
            prerequisites=prerequisite,
            profiles=(current_profile,),
            now=NOW,
            refresh_threshold=timedelta(days=30),
            verifier=verifier,
        )

    assert caught.value.code is ErrorCode.CACHE_REUSE_INVALID
    assert not verifier.called


def test_failed_current_verification_rejects_cache_hit(tmp_path: Path) -> None:
    artifact = tmp_path / "cached.ipa"
    artifact.write_bytes(b"cached signed ipa")
    verifier = RecordingVerifier(fail=True)

    with pytest.raises(DomainError, match="full verification"):
        revalidate_cached_artifact(
            plan=plan(),
            cache_record=cache_record(artifact),
            artifact=artifact,
            prerequisites=ready(),
            profiles=(profile(),),
            now=NOW,
            refresh_threshold=timedelta(days=30),
            verifier=verifier,
        )

    assert verifier.called


@pytest.mark.parametrize("failure", ["record-task", "profile-set", "profile-identity"])
def test_cache_and_profile_identity_must_match_plan(tmp_path: Path, failure: str) -> None:
    artifact = tmp_path / "cached.ipa"
    artifact.write_bytes(b"cached signed ipa")
    record = cache_record(artifact)
    current_profiles: tuple[ProvisioningProfile, ...] = (profile(),)
    if failure == "record-task":
        record = replace(record, task_name="Other")
    elif failure == "profile-set":
        current_profiles = ()
    else:
        current_profiles = (replace(profile(), profile_sha256="0" * 64),)
    verifier = RecordingVerifier()

    with pytest.raises(DomainError):
        revalidate_cached_artifact(
            plan=plan(),
            cache_record=record,
            artifact=artifact,
            prerequisites=ready(),
            profiles=current_profiles,
            now=NOW,
            refresh_threshold=timedelta(days=30),
            verifier=verifier,
        )

    assert not verifier.called
