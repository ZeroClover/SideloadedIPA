"""Canonical serialization, durable writes, digests, redaction, and clock helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from sideloadedipa.domain import Diagnostic, thaw_json


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(
    document: object,
    *,
    default: Callable[[object], object] | None = None,
) -> bytes:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    ).encode()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        temporary = None
        _sync_parent(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_copy(source: Path, destination: Path, *, mode: int = 0o600) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with (
            source.open("rb") as source_handle,
            tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                dir=destination.parent,
                delete=False,
            ) as destination_handle,
        ):
            temporary = Path(destination_handle.name)
            shutil.copyfileobj(source_handle, destination_handle)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
        temporary = None
        _sync_parent(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def redact_text(value: str, redactions: Sequence[str]) -> str:
    for literal in sorted((item for item in redactions if item), key=len, reverse=True):
        value = value.replace(literal, "***")
    return value


def redact_value(value: object, redactions: Sequence[str]) -> object:
    if isinstance(value, str):
        return redact_text(value, redactions)
    if isinstance(value, list):
        return [redact_value(item, redactions) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, redactions) for key, item in value.items()}
    return value


def diagnostic_document(diagnostic: Diagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
        "task_name": diagnostic.task_name,
        "bundle_id": diagnostic.bundle_id,
        "remediation": diagnostic.remediation,
        "details": {key: thaw_json(value) for key, value in diagnostic.details},
    }
