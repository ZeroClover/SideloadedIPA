"""Durable source-selection state and normalized source metadata."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import SourceAsset
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.pipeline.inspection import ResolvedSource
from sideloadedipa.sources import DownloadedSource
from sideloadedipa.util.atomics import atomic_write_bytes, canonical_json


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def source_asset(resolved: ResolvedSource, downloaded: DownloadedSource) -> SourceAsset:
    evidence = resolved.evidence
    asset_id = evidence.get("asset_id")
    name = evidence.get("asset_name")
    version = evidence.get("release_tag")
    return SourceAsset(
        asset_id=str(asset_id) if asset_id is not None else downloaded.sha256[:16],
        name=name if isinstance(name, str) and name else downloaded.path.name,
        source_url=resolved.url,
        version=version if isinstance(version, str) and version else downloaded.sha256[:12],
        published_at=_published_at(evidence.get("published_at")),
        path=PurePosixPath(downloaded.path.name),
        sha256=downloaded.sha256,
    )


def _digest_value(value: str | None) -> str | None:
    if value is None:
        return None
    return value.removeprefix("sha256:").lower()


def validate_downloaded_source(resolved: ResolvedSource, downloaded: DownloadedSource) -> None:
    """Fail closed when persisted source bytes differ from retained evidence."""

    expected_sizes = [resolved.advertised_size]
    retained_size = resolved.evidence.get("actual_size")
    if isinstance(retained_size, int) and not isinstance(retained_size, bool):
        expected_sizes.append(retained_size)
    for expected_size in expected_sizes:
        if expected_size is not None and downloaded.size != expected_size:
            raise ConfigurationError(
                ErrorCode.SOURCE_ADVERTISED_SIZE_MISMATCH,
                "persisted run source size differs from retained source evidence",
                remediation="restart the pipeline with a new run ID",
                safe_details=(
                    ("expected_bytes", expected_size),
                    ("actual_bytes", downloaded.size),
                ),
            )

    expected_digests = [
        _digest_value(resolved.expected_sha256),
        (
            str(resolved.evidence["actual_sha256"]).lower()
            if isinstance(resolved.evidence.get("actual_sha256"), str)
            else None
        ),
    ]
    for expected_digest in expected_digests:
        if expected_digest is not None and downloaded.sha256 != expected_digest:
            raise ConfigurationError(
                ErrorCode.SOURCE_DIGEST_MISMATCH,
                "persisted run source digest differs from retained source evidence",
                remediation="restart the pipeline with a new run ID",
                safe_details=(
                    ("expected", expected_digest),
                    ("actual", downloaded.sha256),
                ),
            )


def bind_download_evidence(
    resolved: ResolvedSource, downloaded: DownloadedSource
) -> ResolvedSource:
    """Bind selected identity and measured bytes for the remainder of one run."""

    validate_downloaded_source(resolved, downloaded)
    advertised_digest = _digest_value(resolved.expected_sha256)
    evidence = dict(resolved.evidence)
    evidence.update(
        {
            "expected_size": resolved.advertised_size,
            "actual_size": downloaded.size,
            "expected_sha256": advertised_digest,
            "actual_sha256": downloaded.sha256,
            "download_attempts": downloaded.attempts,
        }
    )
    return ResolvedSource(
        url=resolved.url,
        expected_sha256=f"sha256:{advertised_digest or downloaded.sha256}",
        evidence=evidence,
        advertised_size=resolved.advertised_size,
    )


def write_source_selection(path: Path, resolved: ResolvedSource) -> None:
    document = {
        "url": resolved.url,
        "expected_sha256": resolved.expected_sha256,
        "evidence": dict(resolved.evidence),
        "advertised_size": resolved.advertised_size,
    }
    atomic_write_bytes(path, canonical_json(document) + b"\n")


def read_source_selection(path: Path) -> ResolvedSource:
    try:
        document = json.loads(path.read_bytes())
        url = document["url"]
        expected_sha256 = document["expected_sha256"]
        evidence = document["evidence"]
        advertised_size = document["advertised_size"]
        if (
            not isinstance(url, str)
            or not url
            or (expected_sha256 is not None and not isinstance(expected_sha256, str))
            or not isinstance(evidence, dict)
            or (advertised_size is not None and not isinstance(advertised_size, int))
            or (
                "actual_size" in evidence
                and (
                    isinstance(evidence["actual_size"], bool)
                    or not isinstance(evidence["actual_size"], int)
                    or evidence["actual_size"] < 0
                )
            )
            or (
                "actual_sha256" in evidence
                and (
                    not isinstance(evidence["actual_sha256"], str)
                    or len(evidence["actual_sha256"]) != 64
                )
            )
            or (
                "download_attempts" in evidence
                and (
                    isinstance(evidence["download_attempts"], bool)
                    or not isinstance(evidence["download_attempts"], int)
                    or evidence["download_attempts"] <= 0
                )
            )
        ):
            raise TypeError
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "persisted source selection is missing or invalid",
            remediation="restart the pipeline with a new run ID",
        ) from error
    return ResolvedSource(url, expected_sha256, evidence, advertised_size)
