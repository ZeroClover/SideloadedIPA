"""Per-task cache records and selective rebuild decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from sideloadedipa.cache.fingerprint import SigningCacheFingerprint
from sideloadedipa.util.atomics import canonical_json

CACHE_INDEX_SCHEMA_VERSION = 2


class RebuildReason(StrEnum):
    CACHE_HIT = "cache-hit"
    CACHE_REJECTED = "cache-rejected"
    FIRST_RUN = "first-run"
    FINGERPRINT_CHANGED = "fingerprint-changed"
    SCHEMA_CHANGED = "schema-changed"
    FORCED = "forced"


@dataclass(frozen=True, slots=True)
class TaskCacheRecord:
    task_name: str
    fingerprint_schema_version: int
    fingerprint_sha256: str
    artifact_sha256: str
    verification_report_sha256: str | None
    signing_report_sha256: str


@dataclass(frozen=True, slots=True)
class CacheIndex:
    schema_version: int
    records: tuple[TaskCacheRecord, ...]
    index_sha256: str


@dataclass(frozen=True, slots=True)
class RebuildDecision:
    task_name: str
    rebuild: bool
    reason: RebuildReason
    fingerprint_sha256: str
    cached_artifact_sha256: str | None = None


def _records_document(records: tuple[TaskCacheRecord, ...]) -> list[dict[str, object]]:
    return [
        {
            "task_name": record.task_name,
            "fingerprint_schema_version": record.fingerprint_schema_version,
            "fingerprint_sha256": record.fingerprint_sha256,
            "artifact_sha256": record.artifact_sha256,
            "verification_report_sha256": record.verification_report_sha256,
            "signing_report_sha256": record.signing_report_sha256,
        }
        for record in sorted(records, key=lambda value: value.task_name)
    ]


def _index_document(index: CacheIndex) -> dict[str, object]:
    return {
        "schema_version": index.schema_version,
        "records": _records_document(index.records),
    }


def build_cache_index(records: tuple[TaskCacheRecord, ...]) -> CacheIndex:
    ordered = tuple(sorted(records, key=lambda value: value.task_name))
    if len({record.task_name for record in ordered}) != len(ordered):
        raise ValueError("cache index contains duplicate task records")
    partial = CacheIndex(CACHE_INDEX_SCHEMA_VERSION, ordered, "")
    return CacheIndex(
        partial.schema_version,
        partial.records,
        hashlib.sha256(canonical_json(_index_document(partial))).hexdigest(),
    )


def canonical_cache_index_json(index: CacheIndex) -> bytes:
    document = _index_document(index)
    if hashlib.sha256(canonical_json(document)).hexdigest() != index.index_sha256:
        raise ValueError("cache index digest is inconsistent with its records")
    document["index_sha256"] = index.index_sha256
    return canonical_json(document)


def parse_cache_index_json(payload: bytes) -> CacheIndex:
    """Parse a digest-verified cache index without accepting partial records."""

    try:
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise TypeError
        records_document = document["records"]
        if not isinstance(records_document, list):
            raise TypeError
        records = tuple(
            TaskCacheRecord(
                task_name=value["task_name"],
                fingerprint_schema_version=value["fingerprint_schema_version"],
                fingerprint_sha256=value["fingerprint_sha256"],
                artifact_sha256=value["artifact_sha256"],
                verification_report_sha256=value["verification_report_sha256"],
                signing_report_sha256=value["signing_report_sha256"],
            )
            for value in records_document
            if isinstance(value, dict)
        )
        if len(records) != len(records_document):
            raise TypeError
        index = CacheIndex(
            schema_version=document["schema_version"],
            records=records,
            index_sha256=document["index_sha256"],
        )
        if not isinstance(index.schema_version, int) or not isinstance(index.index_sha256, str):
            raise TypeError
        if (
            any(
                not isinstance(field, str) or not field
                for record in records
                for field in (
                    record.task_name,
                    record.fingerprint_sha256,
                    record.artifact_sha256,
                    record.signing_report_sha256,
                )
            )
            or any(
                record.verification_report_sha256 is not None
                and (
                    not isinstance(record.verification_report_sha256, str)
                    or not record.verification_report_sha256
                )
                for record in records
            )
            or any(not isinstance(record.fingerprint_schema_version, int) for record in records)
        ):
            raise TypeError
        canonical_cache_index_json(index)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("cache index is invalid or its digest does not match") from error
    return index


def select_rebuilds(
    fingerprints: tuple[SigningCacheFingerprint, ...],
    cached: CacheIndex | None,
    *,
    force: bool = False,
) -> tuple[RebuildDecision, ...]:
    """Select only changed tasks unless force or a cache schema change requires all."""

    ordered = tuple(sorted(fingerprints, key=lambda value: value.task_name))
    if len({value.task_name for value in ordered}) != len(ordered):
        raise ValueError("current fingerprints contain duplicate task names")
    if cached is not None:
        canonical_cache_index_json(cached)
    cached_records = {record.task_name: record for record in cached.records} if cached else {}
    schema_changed = cached is not None and (
        cached.schema_version != CACHE_INDEX_SCHEMA_VERSION
        or any(
            record.fingerprint_schema_version != fingerprint.schema_version
            for fingerprint in ordered
            if (record := cached_records.get(fingerprint.task_name)) is not None
        )
    )

    decisions: list[RebuildDecision] = []
    for fingerprint in ordered:
        record = cached_records.get(fingerprint.task_name)
        if force:
            reason = RebuildReason.FORCED
        elif schema_changed:
            reason = RebuildReason.SCHEMA_CHANGED
        elif record is None:
            reason = RebuildReason.FIRST_RUN
        elif record.verification_report_sha256 is None:
            reason = RebuildReason.CACHE_REJECTED
        elif record.fingerprint_sha256 != fingerprint.sha256:
            reason = RebuildReason.FINGERPRINT_CHANGED
        else:
            reason = RebuildReason.CACHE_HIT
        decisions.append(
            RebuildDecision(
                fingerprint.task_name,
                reason is not RebuildReason.CACHE_HIT,
                reason,
                fingerprint.sha256,
                record.artifact_sha256 if record is not None else None,
            )
        )
    return tuple(decisions)
