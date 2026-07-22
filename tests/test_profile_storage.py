"""Tests for deterministic profile storage and redacted manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import ProfileManifestEntry
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.signing.profile_storage import (
    build_profile_manifest,
    canonical_profile_manifest_json,
    load_profile_manifest,
    profile_manifest_relative_path,
    profile_relative_path,
    store_profile,
    store_profile_manifest,
)


def entry(task_name: str, target_bundle_id: str, profile_id: str) -> ProfileManifestEntry:
    return ProfileManifestEntry(
        target_bundle_id=target_bundle_id,
        bundle_resource_id=f"BUNDLE_{profile_id}",
        profile_resource_id=profile_id,
        certificate_resource_id="CERT_ONE",
        profile_path=profile_relative_path(task_name, target_bundle_id),
        profile_sha256=hashlib.sha256(f"profile:{profile_id}".encode()).hexdigest(),
        device_set_sha256=hashlib.sha256(b"DEVICE_ONE").hexdigest(),
        expires_at=datetime(2027, 7, 21, tzinfo=timezone.utc),
    )


def test_paths_are_deterministic_readable_and_collision_resistant() -> None:
    first = profile_relative_path("Live Container", "io.example.app")
    second = profile_relative_path("Live/Container", "io.example.app")

    assert first == profile_relative_path("Live Container", "io.example.app")
    assert first.parts[0].startswith("Live-Container-")
    assert first.name.startswith("io.example.app-")
    assert first.suffix == ".mobileprovision"
    assert first != second
    assert profile_manifest_relative_path("Live Container").parent == first.parent


def test_manifest_is_canonical_keyed_and_contains_no_raw_material() -> None:
    task_name = "Live Container"
    entries = (
        entry(task_name, "io.example.app.share", "PROFILE_TWO"),
        entry(task_name, "io.example.app", "PROFILE_ONE"),
    )

    manifest = build_profile_manifest(
        task_name=task_name,
        snapshot_sha256="snapshot",
        entries=entries,
    )
    encoded = canonical_profile_manifest_json(manifest)
    document = json.loads(encoded)

    assert [value.target_bundle_id for value in manifest.entries] == [
        "io.example.app",
        "io.example.app.share",
    ]
    assert list(document["profiles"]) == ["io.example.app", "io.example.app.share"]
    assert document["profiles"]["io.example.app"]["profile_resource_id"] == "PROFILE_ONE"
    assert document["manifest_sha256"] == manifest.manifest_sha256
    without_digest = {key: value for key, value in document.items() if key != "manifest_sha256"}
    assert (
        manifest.manifest_sha256
        == hashlib.sha256(
            json.dumps(without_digest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert b"mobileprovision payload" not in encoded
    assert b"UDID" not in encoded


def test_stores_profiles_and_manifest_atomically_with_private_permissions(tmp_path: Path) -> None:
    task_name = "Live Container"
    content = b"private mobileprovision payload"
    relative_path, digest = store_profile(
        tmp_path,
        task_name=task_name,
        target_bundle_id="io.example.app",
        content=content,
    )
    manifest = build_profile_manifest(
        task_name=task_name,
        snapshot_sha256="snapshot",
        entries=(
            replace(
                entry(task_name, "io.example.app", "PROFILE_ONE"),
                profile_sha256=digest,
            ),
        ),
    )
    manifest_path = store_profile_manifest(tmp_path, manifest)

    stored = tmp_path.joinpath(*relative_path.parts)
    assert stored.read_bytes() == content
    assert stored.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(tmp_path.joinpath(*manifest_path.parts).read_bytes())["manifest_sha256"]
        == manifest.manifest_sha256
    )
    assert not list(tmp_path.rglob(".tmp-*"))


def test_loads_only_an_authenticated_task_manifest(tmp_path: Path) -> None:
    task_name = "Live Container"
    manifest = build_profile_manifest(
        task_name=task_name,
        snapshot_sha256="snapshot",
        entries=(entry(task_name, "io.example.app", "PROFILE_ONE"),),
    )
    manifest_path = store_profile_manifest(tmp_path, manifest)

    assert load_profile_manifest(tmp_path, task_name) == manifest

    path = tmp_path.joinpath(*manifest_path.parts)
    document = json.loads(path.read_bytes())
    document["profiles"]["io.example.app"]["profile_resource_id"] = "PROFILE_TAMPERED"
    path.write_text(json.dumps(document))
    with pytest.raises(DomainError) as caught:
        load_profile_manifest(tmp_path, task_name)
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_missing_profile_manifest_requires_sync(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as caught:
        load_profile_manifest(tmp_path, "Missing")

    assert caught.value.code is ErrorCode.CONFIG_MISSING
    assert "sync" in (caught.value.remediation or "")


def test_rejects_duplicate_or_non_deterministic_manifest_entries() -> None:
    task_name = "Task"
    first = entry(task_name, "io.example.app", "PROFILE_ONE")
    duplicate_target = replace(first, profile_resource_id="PROFILE_TWO")
    duplicate_profile = entry(task_name, "io.example.other", "PROFILE_ONE")
    wrong_path = replace(first, profile_path=PurePosixPath("legacy.mobileprovision"))

    for values in ((first, duplicate_target), (first, duplicate_profile), (wrong_path,)):
        with pytest.raises(DomainError) as caught:
            build_profile_manifest(
                task_name=task_name,
                snapshot_sha256="snapshot",
                entries=values,
            )
        assert caught.value.code is ErrorCode.DOMAIN_INVARIANT
