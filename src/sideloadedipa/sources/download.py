"""Streaming source downloads with atomic digest verification."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from sideloadedipa.errors import AdapterError, DomainError, ErrorCode

_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class DownloadedSource:
    path: Path
    size: int
    sha256: str


def _normalize_digest(expected_sha256: str | None) -> str | None:
    if expected_sha256 is None:
        return None
    digest = expected_sha256.removeprefix("sha256:")
    if not _SHA256_PATTERN.fullmatch(digest):
        raise DomainError(
            ErrorCode.SOURCE_DIGEST_INVALID,
            "expected source SHA-256 has invalid syntax",
            remediation="configure a 64-character SHA-256 digest",
        )
    return digest.lower()


def _stream_to_file(
    response: BinaryIO,
    destination: Path,
    *,
    expected_sha256: str | None,
    chunk_size: int,
) -> DownloadedSource:
    if destination.exists():
        raise DomainError(
            ErrorCode.WORKSPACE_INVALID,
            "source destination already exists",
            remediation="use a fresh task workspace",
            safe_details=(("path", destination.name),),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = _normalize_digest(expected_sha256)
    digest = hashlib.sha256()
    size = 0
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            while chunk := response.read(chunk_size):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        actual = digest.hexdigest()
        if expected is not None and actual != expected:
            raise DomainError(
                ErrorCode.SOURCE_DIGEST_MISMATCH,
                "downloaded source digest does not match reviewed evidence",
                remediation="verify the release asset and update the reviewed digest",
                safe_details=(("expected", expected), ("actual", actual)),
            )
        os.replace(temporary_path, destination)
        temporary_path = None
        destination.chmod(0o444)
        return DownloadedSource(path=destination, size=size, sha256=actual)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def download_source_asset(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    timeout_seconds: float = 60,
    chunk_size: int = 1024 * 1024,
) -> DownloadedSource:
    """Download one source asset into a fresh workspace path."""

    request = Request(url, headers={"User-Agent": "SideloadedIPA/1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return _stream_to_file(
                response,
                destination,
                expected_sha256=expected_sha256,
                chunk_size=chunk_size,
            )
    except DomainError:
        raise
    except (OSError, URLError) as error:
        raise AdapterError(
            ErrorCode.SOURCE_DOWNLOAD_FAILED,
            "source asset download failed",
            adapter="urllib",
            operation="download",
            remediation="retry the task or verify the source URL",
        ) from error
