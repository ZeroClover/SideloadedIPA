"""Exact Info.plist identifier rewrites driven only by a validated signing plan."""

from __future__ import annotations

import os
import plistlib
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import BundleNodeKind, SigningPlan
from sideloadedipa.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class BundleIdentifierRewrite:
    source_path: PurePosixPath
    source_bundle_id: str
    target_bundle_id: str


def _invalid(message: str, path: PurePosixPath) -> DomainError:
    return DomainError(
        ErrorCode.SIGNING_PLAN_INVALID,
        message,
        remediation="rebuild and validate the signing plan before modifying the workspace",
        safe_details=(("path", path.as_posix()),),
    )


def _info_path(workspace: Path, source_path: PurePosixPath) -> Path:
    if source_path.is_absolute() or ".." in source_path.parts:
        raise _invalid("planned bundle path is not a safe workspace-relative path", source_path)
    root = workspace.resolve()
    path = root.joinpath(*source_path.parts, "Info.plist").resolve()
    if not path.is_relative_to(root):
        raise _invalid("planned Info.plist path escapes the signing workspace", source_path)
    return path


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-info-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def rewrite_bundle_identifiers(
    workspace: Path, plan: SigningPlan
) -> tuple[BundleIdentifierRewrite, ...]:
    """Rewrite every planned application/extension identifier exactly once."""

    prepared: list[tuple[Path, bytes, int, BundleIdentifierRewrite]] = []
    for node in plan.nodes:
        if node.target_bundle_id is None:
            continue
        if node.kind not in {BundleNodeKind.APP, BundleNodeKind.APP_EXTENSION}:
            raise _invalid(
                "only profile-bearing bundles may have a target identifier", node.source_path
            )
        info_path = _info_path(workspace, node.source_path)
        try:
            raw = info_path.read_bytes()
            mode = info_path.stat().st_mode & 0o777
            document = plistlib.loads(raw)
        except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as error:
            raise _invalid(
                "planned bundle Info.plist could not be decoded", node.source_path
            ) from error
        if not isinstance(document, Mapping):
            raise _invalid("planned bundle Info.plist root is not a dictionary", node.source_path)
        source_bundle_id = document.get("CFBundleIdentifier")
        if not isinstance(source_bundle_id, str) or not source_bundle_id:
            raise _invalid("planned bundle has no valid CFBundleIdentifier", node.source_path)
        rewritten = dict(document)
        rewritten["CFBundleIdentifier"] = node.target_bundle_id
        output_format = plistlib.FMT_BINARY if raw.startswith(b"bplist00") else plistlib.FMT_XML
        try:
            encoded = plistlib.dumps(rewritten, fmt=output_format, sort_keys=False)
        except (TypeError, OverflowError) as error:
            raise _invalid(
                "planned bundle Info.plist could not be encoded", node.source_path
            ) from error
        prepared.append(
            (
                info_path,
                encoded,
                mode,
                BundleIdentifierRewrite(
                    node.source_path,
                    source_bundle_id,
                    node.target_bundle_id,
                ),
            )
        )

    for path, encoded, mode, _ in prepared:
        _atomic_write(path, encoded, mode)
    return tuple(evidence for _, _, _, evidence in prepared)
