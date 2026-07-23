"""Tests for bounded, identity-preserving source downloads."""

from __future__ import annotations

import hashlib
import stat
from email.message import Message
from pathlib import Path
from types import TracebackType
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from sideloadedipa.errors import AdapterError, DomainError, ErrorCode
from sideloadedipa.sources import DownloadPolicy, download_source_asset


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        headers: dict[str, str] | None = None,
        final_url: str = "https://cdn.example/App.ipa",
        error_after_reads: int | None = None,
    ) -> None:
        self.content = content
        self.headers = headers or {}
        self.final_url = final_url
        self.error_after_reads = error_after_reads
        self.offset = 0
        self.read_sizes: list[int] = []

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def geturl(self) -> str:
        return self.final_url

    def read(self, size: int = -1) -> bytes:
        if self.error_after_reads is not None and len(self.read_sizes) >= self.error_after_reads:
            raise OSError("response contained secret-token")
        self.read_sizes.append(size)
        start = self.offset
        self.offset += size
        return self.content[start : self.offset]


class FakeTransport:
    def __init__(self, *outcomes: FakeResponse | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, timeout_seconds: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout_seconds)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def policy(
    *,
    maximum_bytes: int = 1024,
    timeout_seconds: float = 12,
    chunk_bytes: int = 4,
    maximum_attempts: int = 1,
    backoff_seconds: float = 0,
) -> DownloadPolicy:
    return DownloadPolicy(
        maximum_bytes=maximum_bytes,
        timeout_seconds=timeout_seconds,
        chunk_bytes=chunk_bytes,
        maximum_attempts=maximum_attempts,
        backoff_seconds=backoff_seconds,
    )


def test_streams_verifies_and_marks_source_read_only(tmp_path: Path) -> None:
    content = b"streamed IPA bytes"
    expected = hashlib.sha256(content).hexdigest()
    response = FakeResponse(content, headers={"Content-Length": str(len(content))})
    transport = FakeTransport(response)
    destination = tmp_path / "task" / "source.ipa"

    result = download_source_asset(
        "https://example.com/App.ipa",
        destination,
        expected_sha256=f"sha256:{expected.upper()}",
        expected_size=len(content),
        policy=policy(maximum_bytes=128),
        open_url=transport,
    )

    assert result.path == destination
    assert result.size == len(content)
    assert result.sha256 == expected
    assert result.attempts == 1
    assert destination.read_bytes() == content
    assert stat.S_IMODE(destination.stat().st_mode) == 0o444
    assert response.read_sizes == [4, 4, 4, 4, 4, 4]
    assert transport.timeouts == [12]
    assert transport.requests[0].full_url == "https://example.com/App.ipa"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/App.ipa",
        "ftp://example.com/App.ipa",
        "https:///missing-authority.ipa",
    ],
)
def test_rejects_non_https_source_before_transport(url: str, tmp_path: Path) -> None:
    transport = FakeTransport(FakeResponse(b"unused"))

    with pytest.raises(DomainError) as caught:
        download_source_asset(url, tmp_path / "source.ipa", open_url=transport)

    assert caught.value.code is ErrorCode.SOURCE_TRANSPORT_INVALID
    assert transport.requests == []
    assert url not in str(caught.value)


def test_rejects_redirect_downgrade_before_reading(tmp_path: Path) -> None:
    response = FakeResponse(b"secret", final_url="http://cdn.example/App.ipa?token=secret")

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa?token=secret",
            tmp_path / "source.ipa",
            open_url=FakeTransport(response),
        )

    assert caught.value.code is ErrorCode.SOURCE_REDIRECT_REJECTED
    assert response.read_sizes == []
    assert "secret" not in str(caught.value)
    assert "secret" not in repr(caught.value.safe_details)


def test_declared_length_above_policy_is_rejected_before_body(tmp_path: Path) -> None:
    response = FakeResponse(b"012345678", headers={"Content-Length": "9"})

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            tmp_path / "source.ipa",
            policy=policy(maximum_bytes=8),
            open_url=FakeTransport(response),
        )

    assert caught.value.code is ErrorCode.SOURCE_TRANSFER_LIMIT
    assert dict(caught.value.safe_details) == {"declared_bytes": 9, "maximum_bytes": 8}
    assert response.read_sizes == []
    assert list(tmp_path.iterdir()) == []


def test_streamed_limit_removes_temporary_download(tmp_path: Path) -> None:
    response = FakeResponse(b"012345678")
    destination = tmp_path / "source.ipa"

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            destination,
            policy=policy(maximum_bytes=8),
            open_url=FakeTransport(response),
        )

    assert caught.value.code is ErrorCode.SOURCE_TRANSFER_LIMIT
    assert dict(caught.value.safe_details) == {"actual_bytes": 9, "maximum_bytes": 8}
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_advertised_size_mismatch_removes_download(tmp_path: Path) -> None:
    destination = tmp_path / "source.ipa"

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            destination,
            expected_size=8,
            policy=policy(),
            open_url=FakeTransport(FakeResponse(b"seven!!")),
        )

    assert caught.value.code is ErrorCode.SOURCE_ADVERTISED_SIZE_MISMATCH
    assert dict(caught.value.safe_details) == {"expected_bytes": 8, "actual_bytes": 7}
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("expected_size", "error_code"),
    [
        (-1, ErrorCode.SOURCE_ADVERTISED_SIZE_MISMATCH),
        (1025, ErrorCode.SOURCE_TRANSFER_LIMIT),
    ],
)
def test_rejects_invalid_advertised_size_before_transport(
    expected_size: int,
    error_code: ErrorCode,
    tmp_path: Path,
) -> None:
    transport = FakeTransport(FakeResponse(b"unused"))

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            tmp_path / "source.ipa",
            expected_size=expected_size,
            policy=policy(maximum_bytes=1024),
            open_url=transport,
        )

    assert caught.value.code is error_code
    assert transport.requests == []


def test_digest_mismatch_removes_temporary_download(tmp_path: Path) -> None:
    destination = tmp_path / "source.ipa"

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            destination,
            expected_sha256="0" * 64,
            open_url=FakeTransport(FakeResponse(b"unexpected")),
        )

    assert caught.value.code is ErrorCode.SOURCE_DIGEST_MISMATCH
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("digest", ["short", "sha256:not-hex", "g" * 64])
def test_rejects_invalid_expected_digest(tmp_path: Path, digest: str) -> None:
    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            tmp_path / "source.ipa",
            expected_sha256=digest,
            open_url=FakeTransport(FakeResponse(b"data")),
        )

    assert caught.value.code is ErrorCode.SOURCE_DIGEST_INVALID


def test_retry_uses_same_identity_and_fresh_temporary_file(tmp_path: Path) -> None:
    destination = tmp_path / "source.ipa"
    first = FakeResponse(b"partial", error_after_reads=1)
    second = FakeResponse(b"complete")
    transport = FakeTransport(first, second)
    delays: list[float] = []

    result = download_source_asset(
        "https://example.com/App.ipa",
        destination,
        expected_sha256=hashlib.sha256(b"complete").hexdigest(),
        expected_size=8,
        policy=policy(maximum_attempts=2, backoff_seconds=0.25),
        open_url=transport,
        sleep=delays.append,
    )

    assert result.size == 8
    assert result.attempts == 2
    assert [request.full_url for request in transport.requests] == [
        "https://example.com/App.ipa",
        "https://example.com/App.ipa",
    ]
    assert delays == [0.25]
    assert destination.read_bytes() == b"complete"
    assert [path.name for path in tmp_path.iterdir()] == ["source.ipa"]


def test_retry_exhaustion_is_bounded_and_redacted(tmp_path: Path) -> None:
    transport = FakeTransport(
        URLError("secret-token"),
        URLError("secret-token"),
        URLError("secret-token"),
    )
    delays: list[float] = []

    with pytest.raises(AdapterError) as caught:
        download_source_asset(
            "https://example.com/App.ipa?token=secret-token",
            tmp_path / "source.ipa",
            policy=policy(maximum_attempts=3, backoff_seconds=0.5),
            open_url=transport,
            sleep=delays.append,
        )

    assert caught.value.code is ErrorCode.SOURCE_RETRY_EXHAUSTED
    assert dict(caught.value.safe_details)["attempts"] == 3
    assert delays == [0.5, 1.0]
    assert "secret-token" not in str(caught.value)
    assert "secret-token" not in repr(caught.value.safe_details)
    assert list(tmp_path.iterdir()) == []


def test_refuses_to_overwrite_workspace_source(tmp_path: Path) -> None:
    destination = tmp_path / "source.ipa"
    destination.write_bytes(b"existing")

    with pytest.raises(DomainError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            destination,
            open_url=FakeTransport(FakeResponse(b"new")),
        )

    assert caught.value.code is ErrorCode.WORKSPACE_INVALID
    assert destination.read_bytes() == b"existing"


@pytest.mark.parametrize(
    "override",
    [
        {"maximum_bytes": 0},
        {"timeout_seconds": 0},
        {"chunk_bytes": 0},
        {"maximum_attempts": 0},
        {"backoff_seconds": -1},
    ],
)
def test_download_policy_requires_bounded_positive_values(override: dict[str, object]) -> None:
    values: dict[str, object] = {
        "maximum_bytes": 1024,
        "timeout_seconds": 10,
        "chunk_bytes": 4,
        "maximum_attempts": 2,
        "backoff_seconds": 0.1,
    }
    values.update(override)

    with pytest.raises(ValueError):
        DownloadPolicy(**values)  # type: ignore[arg-type]


def test_non_retryable_http_failure_keeps_distinct_transport_error(tmp_path: Path) -> None:
    class NonRetryableTransport:
        def __call__(self, request: Request, timeout_seconds: float) -> FakeResponse:
            raise HTTPError(request.full_url, 404, "secret response", Message(), None)

    with pytest.raises(AdapterError) as caught:
        download_source_asset(
            "https://example.com/App.ipa",
            tmp_path / "source.ipa",
            open_url=NonRetryableTransport(),
        )

    assert caught.value.code is ErrorCode.SOURCE_DOWNLOAD_FAILED
    assert dict(caught.value.safe_details)["status"] == 404
    assert "secret response" not in str(caught.value)


def test_retryable_http_failure_reuses_selected_url(tmp_path: Path) -> None:
    class RetryableTransport:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def __call__(self, request: Request, timeout_seconds: float) -> FakeResponse:
            self.urls.append(request.full_url)
            if len(self.urls) == 1:
                raise HTTPError(request.full_url, 503, "temporary", Message(), None)
            return FakeResponse(b"ready")

    transport = RetryableTransport()
    result = download_source_asset(
        "https://example.com/App.ipa",
        tmp_path / "source.ipa",
        expected_size=5,
        policy=policy(maximum_attempts=2),
        open_url=transport,
    )

    assert result.attempts == 2
    assert transport.urls == [
        "https://example.com/App.ipa",
        "https://example.com/App.ipa",
    ]
