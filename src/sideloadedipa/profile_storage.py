"""Deterministic per-task/per-bundle profile paths and redacted manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import ProfileManifestEntry, ProfileResourceManifest
from sideloadedipa.errors import DomainError, ErrorCode


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


def _canonical_json(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


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
        _canonical_json(_manifest_document(task_name, snapshot_sha256, ordered))
    ).hexdigest()
    return ProfileResourceManifest(1, task_name, snapshot_sha256, ordered, digest)


def canonical_profile_manifest_json(manifest: ProfileResourceManifest) -> bytes:
    document = _manifest_document(manifest.task_name, manifest.snapshot_sha256, manifest.entries)
    document["manifest_sha256"] = manifest.manifest_sha256
    return _canonical_json(document)


def _destination(root: Path, relative_path: PurePosixPath) -> Path:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise DomainError(
            ErrorCode.WORKSPACE_INVALID,
            "profile storage path must be relative and traversal-free",
        )
    return root.joinpath(*relative_path.parts)


def _atomic_write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
