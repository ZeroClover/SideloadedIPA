"""Tests proving cache hits cannot bypass current verification."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.cache.decisions import TaskCacheRecord
from sideloadedipa.cache.reuse import CachePrerequisiteState, revalidate_cached_artifact
from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    ProfileType,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode

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
        "9" * 64,
        "1" * 64,
    )


def ready() -> CachePrerequisiteState:
    return CachePrerequisiteState(True, "2" * 64)


def test_cache_hit_checks_current_prerequisites_and_artifact_digest(tmp_path: Path) -> None:
    artifact = tmp_path / "cached.ipa"
    artifact.write_bytes(b"cached signed ipa")

    result = revalidate_cached_artifact(
        plan=plan(),
        cache_record=cache_record(artifact),
        artifact=artifact,
        prerequisites=ready(),
        profiles=(profile(),),
        now=NOW,
        refresh_threshold=timedelta(days=30),
    )

    assert result == hashlib.sha256(artifact.read_bytes()).hexdigest()


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
    with pytest.raises(DomainError) as caught:
        revalidate_cached_artifact(
            plan=plan(),
            cache_record=record,
            artifact=artifact,
            prerequisites=prerequisite,
            profiles=(current_profile,),
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )

    assert caught.value.code is ErrorCode.CACHE_REUSE_INVALID


def test_previous_verification_digest_is_not_reused_as_the_current_gate(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "cached.ipa"
    artifact.write_bytes(b"cached signed ipa")
    record = replace(cache_record(artifact), verification_report_sha256="0" * 64)

    result = revalidate_cached_artifact(
        plan=plan(),
        cache_record=record,
        artifact=artifact,
        prerequisites=ready(),
        profiles=(profile(),),
        now=NOW,
        refresh_threshold=timedelta(days=30),
    )

    assert result == record.artifact_sha256


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
    with pytest.raises(DomainError):
        revalidate_cached_artifact(
            plan=plan(),
            cache_record=record,
            artifact=artifact,
            prerequisites=ready(),
            profiles=current_profiles,
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )
