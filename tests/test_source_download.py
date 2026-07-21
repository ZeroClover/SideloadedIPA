"""Tests for streaming verified source downloads."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from types import TracebackType
from unittest.mock import patch
from urllib.error import URLError

import pytest

from sideloadedipa.errors import AdapterError, DomainError, ErrorCode
from sideloadedipa.sources import download_source_asset


class FakeResponse:
    def __init__(self, content: bytes, error: OSError | None = None) -> None:
        self.content = content
        self.error = error
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

    def read(self, size: int = -1) -> bytes:
        if self.error is not None:
            raise self.error
        self.read_sizes.append(size)
        start = self.offset
        self.offset += size
        return self.content[start : self.offset]


def test_streams_verifies_and_marks_source_read_only(tmp_path: Path) -> None:
    content = b"streamed IPA bytes"
    expected = hashlib.sha256(content).hexdigest()
    response = FakeResponse(content)
    destination = tmp_path / "task" / "source.ipa"

    with patch("sideloadedipa.sources.download.urlopen", return_value=response) as opener:
        result = download_source_asset(
            "https://example.com/App.ipa",
            destination,
            expected_sha256=f"sha256:{expected.upper()}",
            timeout_seconds=12,
            chunk_size=4,
        )

    assert result.path == destination
    assert result.size == len(content)
    assert result.sha256 == expected
    assert destination.read_bytes() == content
    assert stat.S_IMODE(destination.stat().st_mode) == 0o444
    assert response.read_sizes == [4, 4, 4, 4, 4, 4]
    assert opener.call_args.kwargs == {"timeout": 12}


def test_digest_mismatch_removes_temporary_download(tmp_path: Path) -> None:
    destination = tmp_path / "source.ipa"
    response = FakeResponse(b"unexpected")

    with (
        patch("sideloadedipa.sources.download.urlopen", return_value=response),
        pytest.raises(DomainError) as caught,
    ):
        download_source_asset(
            "https://example.com/App.ipa",
            destination,
            expected_sha256="0" * 64,
        )

    assert caught.value.code is ErrorCode.SOURCE_DIGEST_MISMATCH
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("digest", ["short", "sha256:not-hex", "g" * 64])
def test_rejects_invalid_expected_digest(tmp_path: Path, digest: str) -> None:
    with (
        patch("sideloadedipa.sources.download.urlopen", return_value=FakeResponse(b"data")),
        pytest.raises(DomainError) as caught,
    ):
        download_source_asset(
            "https://example.com/App.ipa",
            tmp_path / "source.ipa",
            expected_sha256=digest,
        )

    assert caught.value.code is ErrorCode.SOURCE_DIGEST_INVALID


def test_refuses_to_overwrite_workspace_source(tmp_path: Path) -> None:
    destination = tmp_path / "source.ipa"
    destination.write_bytes(b"existing")

    with (
        patch("sideloadedipa.sources.download.urlopen", return_value=FakeResponse(b"new")),
        pytest.raises(DomainError) as caught,
    ):
        download_source_asset("https://example.com/App.ipa", destination)

    assert caught.value.code is ErrorCode.WORKSPACE_INVALID
    assert destination.read_bytes() == b"existing"


@pytest.mark.parametrize(
    "failure",
    [URLError("offline"), OSError("read failed")],
)
def test_maps_transport_and_stream_failures(tmp_path: Path, failure: BaseException) -> None:
    if isinstance(failure, URLError):
        opened: object = failure
    else:
        opened = FakeResponse(b"", error=failure)

    with patch("sideloadedipa.sources.download.urlopen") as opener:
        if isinstance(opened, BaseException):
            opener.side_effect = opened
        else:
            opener.return_value = opened
        with pytest.raises(AdapterError) as caught:
            download_source_asset("https://example.com/App.ipa", tmp_path / "source.ipa")

    assert caught.value.code is ErrorCode.SOURCE_DOWNLOAD_FAILED
    assert caught.value.adapter == "urllib"
