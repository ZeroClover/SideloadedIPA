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
        ):
            raise TypeError
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "persisted source selection is missing or invalid",
            remediation="restart the pipeline with a new run ID",
        ) from error
    return ResolvedSource(url, expected_sha256, evidence, advertised_size)
