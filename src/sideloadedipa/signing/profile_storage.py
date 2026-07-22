"""Deterministic per-task/per-bundle profile paths and redacted manifests."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

from sideloadedipa.domain import ProfileManifestEntry, ProfileResourceManifest
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.util.atomics import atomic_write_bytes, canonical_json


def _component(value: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "item"
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{readable}-{digest}"


def profile_relative_path(task_name: str, target_bundle_id: str) -> PurePosixPath:
    return PurePosixPath(_component(task_name), f"{_component(target_bundle_id)}.mobileprovision")


def profile_manifest_relative_path(task_name: str) -> PurePosixPath:
    return PurePosixPath(_component(task_name), "resource-manifest.json")


def _manifest_document(
    task_name: str,
    snapshot_sha256: str,
    entries: tuple[ProfileManifestEntry, ...],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_name": task_name,
        "snapshot_sha256": snapshot_sha256,
        "profiles": {
            entry.target_bundle_id: {
                **asdict(entry),
                "profile_path": entry.profile_path.as_posix(),
                "expires_at": entry.expires_at.isoformat(),
                "target_bundle_id": entry.target_bundle_id,
            }
            for entry in entries
        },
    }


def build_profile_manifest(
    *,
    task_name: str,
    snapshot_sha256: str,
    entries: tuple[ProfileManifestEntry, ...],
) -> ProfileResourceManifest:
    if not task_name or not snapshot_sha256:
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "profile manifest requires task and snapshot identities",
        )
    ordered = tuple(
        sorted(
            entries,
            key=lambda value: (value.target_bundle_id.casefold(), value.profile_resource_id),
        )
    )
    targets = [value.target_bundle_id.casefold() for value in ordered]
    profile_ids = [value.profile_resource_id for value in ordered]
    if len(set(targets)) != len(targets) or len(set(profile_ids)) != len(profile_ids):
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "profile manifest requires one target and one stable profile resource per entry",
            task_name=task_name,
            remediation="resolve duplicate target or profile mappings before storage",
        )
    for entry in ordered:
        expected_path = profile_relative_path(task_name, entry.target_bundle_id)
        if entry.profile_path != expected_path:
            raise DomainError(
                ErrorCode.DOMAIN_INVARIANT,
                "profile manifest entry path is not deterministic",
                task_name=task_name,
                bundle_id=entry.target_bundle_id,
                remediation="derive the profile path with profile_relative_path",
            )
    digest = hashlib.sha256(
        canonical_json(_manifest_document(task_name, snapshot_sha256, ordered))
    ).hexdigest()
    return ProfileResourceManifest(1, task_name, snapshot_sha256, ordered, digest)


def canonical_profile_manifest_json(manifest: ProfileResourceManifest) -> bytes:
    document = _manifest_document(manifest.task_name, manifest.snapshot_sha256, manifest.entries)
    document["manifest_sha256"] = manifest.manifest_sha256
    return canonical_json(document)


def load_profile_manifest(root: Path, task_name: str) -> ProfileResourceManifest:
    """Load and authenticate the deterministic manifest written by profile sync."""

    relative_path = profile_manifest_relative_path(task_name)
    path = _destination(root, relative_path)
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise DomainError(
            ErrorCode.CONFIG_MISSING,
            "profile resource manifest is missing or unreadable",
            task_name=task_name,
            remediation="run the package profile sync stage before signing",
            safe_details=(("path_name", path.name),),
        ) from error
    if not isinstance(raw, dict):
        raise DomainError(
            ErrorCode.CONFIG_INVALID,
            "profile resource manifest root must be an object",
            task_name=task_name,
        )
    document = cast(dict[str, object], raw)
    schema_version = document.get("schema_version")
    manifest_task = document.get("task_name")
    snapshot_sha256 = document.get("snapshot_sha256")
    manifest_sha256 = document.get("manifest_sha256")
    profiles = document.get("profiles")
    if (
        schema_version != 1
        or manifest_task != task_name
        or not isinstance(snapshot_sha256, str)
        or not isinstance(manifest_sha256, str)
        or not isinstance(profiles, dict)
    ):
        raise DomainError(
            ErrorCode.CONFIG_INVALID,
            "profile resource manifest metadata is invalid",
            task_name=task_name,
            remediation="discard the manifest and rerun package profile sync",
        )

    entries: list[ProfileManifestEntry] = []
    for target_bundle_id, value in profiles.items():
        if not isinstance(target_bundle_id, str) or not isinstance(value, dict):
            raise DomainError(
                ErrorCode.CONFIG_INVALID,
                "profile resource manifest contains an invalid profile entry",
                task_name=task_name,
            )
        required = (
            "bundle_resource_id",
            "profile_resource_id",
            "certificate_resource_id",
            "profile_path",
            "profile_sha256",
            "device_set_sha256",
            "expires_at",
        )
        if any(not isinstance(value.get(field), str) for field in required):
            raise DomainError(
                ErrorCode.CONFIG_INVALID,
                "profile resource manifest entry metadata is invalid",
                task_name=task_name,
                bundle_id=target_bundle_id,
            )
        try:
            expires_at = datetime.fromisoformat(cast(str, value["expires_at"]))
        except ValueError as error:
            raise DomainError(
                ErrorCode.CONFIG_INVALID,
                "profile resource manifest expiry is invalid",
                task_name=task_name,
                bundle_id=target_bundle_id,
            ) from error
        entries.append(
            ProfileManifestEntry(
                target_bundle_id,
                cast(str, value["bundle_resource_id"]),
                cast(str, value["profile_resource_id"]),
                cast(str, value["certificate_resource_id"]),
                PurePosixPath(cast(str, value["profile_path"])),
                cast(str, value["profile_sha256"]),
                cast(str, value["device_set_sha256"]),
                expires_at,
            )
        )
    manifest = build_profile_manifest(
        task_name=task_name,
        snapshot_sha256=snapshot_sha256,
        entries=tuple(entries),
    )
    if manifest.manifest_sha256 != manifest_sha256:
        raise DomainError(
            ErrorCode.CONFIG_INVALID,
            "profile resource manifest digest does not match its contents",
            task_name=task_name,
            remediation="discard the manifest and rerun package profile sync",
        )
    return manifest


def _destination(root: Path, relative_path: PurePosixPath) -> Path:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise DomainError(
            ErrorCode.WORKSPACE_INVALID,
            "profile storage path must be relative and traversal-free",
        )
    return root.joinpath(*relative_path.parts)


def _atomic_write(destination: Path, content: bytes) -> None:
    atomic_write_bytes(destination, content)


def store_profile(
    root: Path, *, task_name: str, target_bundle_id: str, content: bytes
) -> tuple[PurePosixPath, str]:
    relative_path = profile_relative_path(task_name, target_bundle_id)
    _atomic_write(_destination(root, relative_path), content)
    return relative_path, hashlib.sha256(content).hexdigest()


def store_profile_manifest(root: Path, manifest: ProfileResourceManifest) -> PurePosixPath:
    relative_path = profile_manifest_relative_path(manifest.task_name)
    _atomic_write(_destination(root, relative_path), canonical_profile_manifest_json(manifest))
    return relative_path
