"""Tests for private package signing inputs produced by profile sync."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sideloadedipa.domain import (
    CertificateIdentity,
    ProfileManifestEntry,
    ProfileType,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.profile_storage import build_profile_manifest, profile_relative_path
from sideloadedipa.signing_inputs import load_synced_profile, load_synced_profiles

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
EXPIRES = NOW + timedelta(days=90)
TEAM_ID = "TEAMID1234"
BUNDLE_ID = "com.example.app"
PREFIX = "PREFIX."
CERTIFICATE_DER = b"certificate"
PROFILE_CONTENT = b"signed-profile"


def certificate() -> CertificateIdentity:
    return CertificateIdentity(
        "CERT_ONE",
        TEAM_ID,
        "1234",
        "a" * 64,
        hashlib.sha256(CERTIFICATE_DER).hexdigest(),
        EXPIRES,
    )


def entry(task_name: str = "Example") -> ProfileManifestEntry:
    return ProfileManifestEntry(
        BUNDLE_ID,
        "BUNDLE_ONE",
        "PROFILE_ONE",
        "CERT_ONE",
        profile_relative_path(task_name, BUNDLE_ID),
        hashlib.sha256(PROFILE_CONTENT).hexdigest(),
        "b" * 64,
        EXPIRES,
    )


def document() -> dict[str, object]:
    return {
        "Name": "Example Dev",
        "TeamIdentifier": [TEAM_ID],
        "ApplicationIdentifierPrefix": [PREFIX.rstrip(".")],
        "DeveloperCertificates": [CERTIFICATE_DER],
        "ProvisionedDevices": ["DEVICE-ONE"],
        "CreationDate": NOW - timedelta(days=1),
        "ExpirationDate": EXPIRES,
        "Entitlements": {
            "application-identifier": f"{PREFIX}{BUNDLE_ID}",
            "com.apple.developer.team-identifier": TEAM_ID,
            "get-task-allow": True,
        },
    }


def decoder(path: Path, *, bundle_id: str | None = None) -> dict[str, object]:
    assert path.read_bytes() == PROFILE_CONTENT
    assert bundle_id == BUNDLE_ID
    return document()


def store_profile(root: Path, value: ProfileManifestEntry) -> None:
    path = root.joinpath(*value.profile_path.parts)
    path.parent.mkdir(parents=True)
    path.write_bytes(PROFILE_CONTENT)


def test_loads_and_revalidates_profile_from_authenticated_entry(tmp_path: Path) -> None:
    value = entry()
    store_profile(tmp_path, value)

    profile = load_synced_profile(
        profile_root=tmp_path,
        entry=value,
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        certificate=certificate(),
        now=NOW,
        decoder=decoder,
    )

    assert profile.resource_id == "PROFILE_ONE"
    assert profile.bundle_id == BUNDLE_ID
    assert profile.profile_sha256 == value.profile_sha256
    assert profile.expires_at == value.expires_at


def test_rejects_manifest_drift_before_decoding(tmp_path: Path) -> None:
    value = entry()
    store_profile(tmp_path, value)

    for changed in (
        replace(value, certificate_resource_id="CERT_TWO"),
        replace(value, profile_sha256="0" * 64),
        replace(value, expires_at=EXPIRES + timedelta(days=1)),
    ):
        with pytest.raises(DomainError) as caught:
            load_synced_profile(
                profile_root=tmp_path,
                entry=changed,
                profile_type=ProfileType.IOS_APP_DEVELOPMENT,
                certificate=certificate(),
                now=NOW,
                decoder=decoder,
            )
        assert caught.value.code is ErrorCode.APPLE_PROFILE_INVALID


def test_loads_all_profiles_and_rejects_unknown_entitlement_target(tmp_path: Path) -> None:
    value = entry()
    store_profile(tmp_path, value)
    manifest = build_profile_manifest(
        task_name="Example",
        snapshot_sha256="snapshot",
        entries=(value,),
    )
    expected = normalize_entitlements(document()["Entitlements"])

    profiles = load_synced_profiles(
        profile_root=tmp_path,
        manifest=manifest,
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        certificate=certificate(),
        now=NOW,
        expected_entitlements={BUNDLE_ID: expected.values},
        decoder=decoder,
    )
    assert len(profiles) == 1

    with pytest.raises(DomainError) as caught:
        load_synced_profiles(
            profile_root=tmp_path,
            manifest=manifest,
            profile_type=ProfileType.IOS_APP_DEVELOPMENT,
            certificate=certificate(),
            now=NOW,
            expected_entitlements={"com.example.unknown": expected.values},
            decoder=decoder,
        )
    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
