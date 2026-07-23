"""Bounded HTTPS source downloads with atomic integrity verification."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sideloadedipa.errors import AdapterError, DomainError, ErrorCode

_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    maximum_bytes: int
    timeout_seconds: float
    chunk_bytes: int
    maximum_attempts: int
    backoff_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.maximum_bytes, bool) or self.maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        if isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(self.chunk_bytes, bool) or self.chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")
        if isinstance(self.maximum_attempts, bool) or self.maximum_attempts <= 0:
            raise ValueError("maximum_attempts must be positive")
        if isinstance(self.backoff_seconds, bool) or self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")


DEFAULT_DOWNLOAD_POLICY = DownloadPolicy(
    maximum_bytes=256 * 1024 * 1024,
    timeout_seconds=60,
    chunk_bytes=1024 * 1024,
    maximum_attempts=3,
    backoff_seconds=0.5,
)


@dataclass(frozen=True, slots=True)
class DownloadedSource:
    path: Path
    size: int
    sha256: str
    attempts: int = 1


class DownloadResponse(Protocol):
    headers: Mapping[str, str]

    def __enter__(self) -> DownloadResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def geturl(self) -> str: ...

    def read(self, size: int = -1) -> bytes: ...


class OpenUrl(Protocol):
    def __call__(self, request: Request, timeout_seconds: float) -> DownloadResponse: ...


def _require_https(url: str, *, redirect: bool) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DomainError(
            ErrorCode.SOURCE_REDIRECT_REJECTED if redirect else ErrorCode.SOURCE_TRANSPORT_INVALID,
            (
                "source redirect must retain authenticated HTTPS transport"
                if redirect
                else "source URL must use HTTPS with a valid authority"
            ),
            remediation="use an HTTPS source without embedded credentials",
        )


class _HttpsOnlyRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        _require_https(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(request: Request, timeout_seconds: float) -> DownloadResponse:
    response = build_opener(_HttpsOnlyRedirectHandler()).open(
        request,
        timeout=timeout_seconds,
    )
    return cast(DownloadResponse, response)


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


def _declared_size(response: DownloadResponse) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        declared = int(value)
    except ValueError:
        return None
    return declared if declared >= 0 else None


def _stream_to_file(
    response: DownloadResponse,
    destination: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
    policy: DownloadPolicy,
) -> DownloadedSource:
    declared = _declared_size(response)
    if declared is not None and declared > policy.maximum_bytes:
        raise DomainError(
            ErrorCode.SOURCE_TRANSFER_LIMIT,
            "declared source size exceeds the reviewed transfer limit",
            remediation="review the source asset and package-owned byte policy",
            safe_details=(
                ("declared_bytes", declared),
                ("maximum_bytes", policy.maximum_bytes),
            ),
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
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
            while chunk := response.read(policy.chunk_bytes):
                size += len(chunk)
                if size > policy.maximum_bytes:
                    raise DomainError(
                        ErrorCode.SOURCE_TRANSFER_LIMIT,
                        "streamed source exceeds the reviewed transfer limit",
                        remediation="review the source asset and package-owned byte policy",
                        safe_details=(
                            ("actual_bytes", size),
                            ("maximum_bytes", policy.maximum_bytes),
                        ),
                    )
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        if expected_size is not None and size != expected_size:
            raise DomainError(
                ErrorCode.SOURCE_ADVERTISED_SIZE_MISMATCH,
                "downloaded source size differs from selected asset evidence",
                remediation="start a new inspect run and review the immutable release asset",
                safe_details=(
                    ("expected_bytes", expected_size),
                    ("actual_bytes", size),
                ),
            )
        actual = digest.hexdigest()
        if expected_sha256 is not None and actual != expected_sha256:
            raise DomainError(
                ErrorCode.SOURCE_DIGEST_MISMATCH,
                "downloaded source digest does not match reviewed evidence",
                remediation="verify the release asset and update the reviewed digest",
                safe_details=(("expected", expected_sha256), ("actual", actual)),
            )
        os.replace(temporary_path, destination)
        temporary_path = None
        destination.chmod(0o444)
        return DownloadedSource(path=destination, size=size, sha256=actual)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _transport_failure(status: int | None = None) -> AdapterError:
    details = (("status", status),) if status is not None else ()
    return AdapterError(
        ErrorCode.SOURCE_DOWNLOAD_FAILED,
        "source asset request failed",
        adapter="urllib",
        operation="download",
        remediation="retry with a new inspect run or verify source availability",
        safe_details=details,
    )


def _retry_exhausted(attempts: int) -> AdapterError:
    return AdapterError(
        ErrorCode.SOURCE_RETRY_EXHAUSTED,
        "source download exhausted its bounded retry policy",
        adapter="urllib",
        operation="download",
        remediation="start a new inspect run after source availability is restored",
        safe_details=(("attempts", attempts),),
    )


def download_source_asset(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    policy: DownloadPolicy = DEFAULT_DOWNLOAD_POLICY,
    open_url: OpenUrl = _open_url,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadedSource:
    """Download one immutable source identity into a fresh workspace path."""

    _require_https(url, redirect=False)
    expected = _normalize_digest(expected_sha256)
    if expected_size is not None and expected_size < 0:
        raise DomainError(
            ErrorCode.SOURCE_ADVERTISED_SIZE_MISMATCH,
            "selected asset size must not be negative",
            remediation="start a new inspect run and review the release response",
            safe_details=(("expected_bytes", expected_size),),
        )
    if expected_size is not None and expected_size > policy.maximum_bytes:
        raise DomainError(
            ErrorCode.SOURCE_TRANSFER_LIMIT,
            "advertised source size exceeds the reviewed transfer limit",
            remediation="review the source asset and package-owned byte policy",
            safe_details=(
                ("advertised_bytes", expected_size),
                ("maximum_bytes", policy.maximum_bytes),
            ),
        )
    if destination.exists():
        raise DomainError(
            ErrorCode.WORKSPACE_INVALID,
            "source destination already exists",
            remediation="use a fresh task workspace",
            safe_details=(("path", destination.name),),
        )

    request = Request(url, headers={"User-Agent": "SideloadedIPA/1"})
    for attempt in range(1, policy.maximum_attempts + 1):
        try:
            with open_url(request, policy.timeout_seconds) as response:
                _require_https(response.geturl(), redirect=True)
                downloaded = _stream_to_file(
                    response,
                    destination,
                    expected_sha256=expected,
                    expected_size=expected_size,
                    policy=policy,
                )
                return replace(downloaded, attempts=attempt)
        except (DomainError, AdapterError):
            raise
        except HTTPError as error:
            if error.code not in _RETRYABLE_HTTP_STATUS:
                raise _transport_failure(error.code) from error
            if attempt == policy.maximum_attempts:
                raise _retry_exhausted(attempt) from error
        except (OSError, URLError) as error:
            if attempt == policy.maximum_attempts:
                raise _retry_exhausted(attempt) from error
        sleep(policy.backoff_seconds * attempt)

    raise _retry_exhausted(policy.maximum_attempts)  # pragma: no cover
