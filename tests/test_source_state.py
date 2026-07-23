"""Tests for durable source-selection state and source metadata normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.pipeline.inspection import ResolvedSource
from sideloadedipa.pipeline.source_state import (
    bind_download_evidence,
    read_source_selection,
    source_asset,
    validate_downloaded_source,
    write_source_selection,
)
from sideloadedipa.sources import DownloadedSource


def test_source_asset_uses_release_evidence_and_safe_fallbacks(tmp_path: Path) -> None:
    downloaded = DownloadedSource(tmp_path / "App.ipa", 10, "a" * 64)
    resolved = ResolvedSource(
        "https://example.test/App.ipa",
        "a" * 64,
        {
            "asset_id": 42,
            "asset_name": "Release.ipa",
            "release_tag": "v1.2.3",
            "published_at": "2026-07-22T01:02:03Z",
        },
        10,
    )

    asset = source_asset(resolved, downloaded)

    assert asset.asset_id == "42"
    assert asset.name == "Release.ipa"
    assert asset.version == "v1.2.3"
    assert asset.published_at == datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc)

    fallback = source_asset(
        ResolvedSource(resolved.url, None, {"published_at": "invalid"}, None),
        downloaded,
    )
    assert fallback.asset_id == "a" * 16
    assert fallback.name == "App.ipa"
    assert fallback.version == "a" * 12
    assert fallback.published_at is None


def test_source_selection_round_trips_and_rejects_invalid_state(tmp_path: Path) -> None:
    path = tmp_path / "source-selection.json"
    expected = ResolvedSource(
        "https://example.test/App.ipa",
        None,
        {"release_tag": "v1"},
        123,
    )

    write_source_selection(path, expected)

    assert read_source_selection(path) == expected
    assert path.read_bytes().endswith(b"\n")

    path.write_text('{"url":"","expected_sha256":1,"evidence":[],"advertised_size":"large"}')
    with pytest.raises(ConfigurationError) as invalid:
        read_source_selection(path)
    assert invalid.value.code is ErrorCode.CONFIG_INVALID

    with pytest.raises(ConfigurationError) as missing:
        read_source_selection(tmp_path / "missing.json")
    assert missing.value.code is ErrorCode.CONFIG_INVALID


def test_download_evidence_binds_advertised_and_actual_identity(tmp_path: Path) -> None:
    digest = "a" * 64
    downloaded = DownloadedSource(tmp_path / "App.ipa", 123, digest, attempts=2)
    resolved = ResolvedSource(
        "https://example.test/App.ipa",
        f"sha256:{digest}",
        {"asset_id": "42", "release_tag": "v1"},
        123,
    )

    bound = bind_download_evidence(resolved, downloaded)

    assert bound.expected_sha256 == f"sha256:{digest}"
    assert bound.evidence == {
        "asset_id": "42",
        "release_tag": "v1",
        "expected_size": 123,
        "actual_size": 123,
        "expected_sha256": digest,
        "actual_sha256": digest,
        "download_attempts": 2,
    }
    validate_downloaded_source(bound, downloaded)


def test_measured_digest_binds_run_when_github_has_no_digest(tmp_path: Path) -> None:
    digest = "b" * 64
    downloaded = DownloadedSource(tmp_path / "App.ipa", 4, digest)
    resolved = ResolvedSource(
        "https://example.test/App.ipa",
        None,
        {"asset_id": "42", "advertised_sha256": None},
        4,
    )

    bound = bind_download_evidence(resolved, downloaded)

    assert bound.expected_sha256 == f"sha256:{digest}"
    assert bound.evidence["expected_sha256"] is None
    assert bound.evidence["actual_sha256"] == digest

    with pytest.raises(ConfigurationError) as resized:
        validate_downloaded_source(
            bound,
            DownloadedSource(downloaded.path, downloaded.size + 1, digest),
        )
    assert resized.value.code is ErrorCode.SOURCE_ADVERTISED_SIZE_MISMATCH

    with pytest.raises(ConfigurationError) as changed:
        validate_downloaded_source(
            bound,
            DownloadedSource(downloaded.path, downloaded.size, "c" * 64),
        )
    assert changed.value.code is ErrorCode.SOURCE_DIGEST_MISMATCH
