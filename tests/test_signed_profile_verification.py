"""Tests for signed bundle and embedded-profile identity verification."""

from __future__ import annotations

import hashlib
import plistlib
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    BundleNodeKind,
    ProfileType,
    ProfileValidationRequest,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.verification.profiles import verify_signed_profiles

ROOT = PurePosixPath("Payload/App.app")
PROFILE_CONTENT = b"signed embedded profile"
NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def profile() -> ProvisioningProfile:
    entitlements = normalize_entitlements(
        {
            "application-identifier": "TEAMID.io.example.app",
            "com.apple.developer.team-identifier": "TEAMID",
        }
    )
    return ProvisioningProfile(
        "PROFILE",
        "Example Dev",
        ProfileType.IOS_APP_DEVELOPMENT,
        "io.example.app",
        "TEAMID.io.example.app",
        "TEAMID",
        "c" * 64,
        ("device-sha256",),
        NOW,
        NOW + timedelta(days=90),
        hashlib.sha256(PROFILE_CONTENT).hexdigest(),
        PurePosixPath("Example/profile.mobileprovision"),
        entitlements.values,
    )


def plan() -> SigningPlan:
    planned_profile = profile()
    return SigningPlan(
        "Example",
        "a" * 64,
        "b" * 64,
        planned_profile.certificate_sha256,
        SigningBackendIdentity("fixture", "1", "d" * 64, "1"),
        (
            SigningNodePlan(
                ROOT,
                ROOT / "App",
                BundleNodeKind.APP,
                0,
                planned_profile.bundle_id,
                planned_profile.resource_id,
                planned_profile.path,
                planned_profile.profile_sha256,
                planned_profile.entitlements,
                normalize_entitlements(dict(planned_profile.entitlements)).sha256,
            ),
        ),
        "e" * 64,
    )


def write_ipa(
    path: Path,
    *,
    bundle_id: str = "io.example.app",
    profile_content: bytes | None = PROFILE_CONTENT,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Payload/App.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": bundle_id}),
        )
        if profile_content is not None:
            archive.writestr("Payload/App.app/embedded.mobileprovision", profile_content)


@dataclass
class FixtureValidator:
    result: ProvisioningProfile
    failure: bool = False
    request: ProfileValidationRequest | None = None

    def validate(self, path: Path, request: ProfileValidationRequest) -> ProvisioningProfile:
        assert path.is_file()
        self.request = request
        if self.failure:
            raise DomainError(ErrorCode.APPLE_PROFILE_INVALID, "profile expired")
        return self.result


def test_verifies_bundle_profile_team_certificate_devices_and_dates(tmp_path: Path) -> None:
    artifact = tmp_path / "signed.ipa"
    write_ipa(artifact)
    validator = FixtureValidator(profile())
    extracted = tmp_path / "signed"
    extract_ipa_safely(artifact, extracted)

    findings = verify_signed_profiles(plan(), extracted, (profile(),), validator=validator)

    assert findings and all(value.passed for value in findings)
    assert validator.request is not None
    assert validator.request.target_bundle_id == "io.example.app"
    assert validator.request.team_id == "TEAMID"
    assert validator.request.certificate_sha256 == "c" * 64
    assert validator.request.device_udid_sha256 == ("device-sha256",)


def test_reports_wrong_bundle_missing_or_changed_profile_and_expiry(tmp_path: Path) -> None:
    wrong = tmp_path / "wrong.ipa"
    missing = tmp_path / "missing.ipa"
    expired = tmp_path / "expired.ipa"
    write_ipa(wrong, bundle_id="io.example.other", profile_content=b"changed")
    write_ipa(missing, profile_content=None)
    write_ipa(expired)
    wrong_root = tmp_path / "wrong"
    missing_root = tmp_path / "missing"
    expired_root = tmp_path / "expired"
    extract_ipa_safely(wrong, wrong_root)
    extract_ipa_safely(missing, missing_root)
    extract_ipa_safely(expired, expired_root)

    wrong_findings = verify_signed_profiles(
        plan(), wrong_root, (profile(),), validator=FixtureValidator(profile())
    )
    missing_findings = verify_signed_profiles(
        plan(), missing_root, (profile(),), validator=FixtureValidator(profile())
    )
    expired_findings = verify_signed_profiles(
        plan(), expired_root, (profile(),), validator=FixtureValidator(profile(), failure=True)
    )

    assert {value.check for value in wrong_findings if not value.passed} == {
        "bundle-identifier",
        "embedded-profile-sha256",
    }
    assert {value.check for value in missing_findings if not value.passed} == {
        "embedded-profile-sha256",
        "embedded-profile-validation",
    }
    assert {value.check for value in expired_findings if not value.passed} == {
        "embedded-profile-validation"
    }


def test_rejects_validator_result_for_another_certificate(tmp_path: Path) -> None:
    artifact = tmp_path / "signed.ipa"
    write_ipa(artifact)
    wrong = replace(profile(), certificate_sha256="0" * 64)
    extracted = tmp_path / "signed"
    extract_ipa_safely(artifact, extracted)

    findings = verify_signed_profiles(
        plan(), extracted, (profile(),), validator=FixtureValidator(wrong)
    )

    assert any(
        value.check == "embedded-profile-validation" and not value.passed for value in findings
    )
