"""Tests for fingerprint-based selective rebuild decisions."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sideloadedipa.cache.decisions import (
    CACHE_INDEX_SCHEMA_VERSION,
    RebuildReason,
    TaskCacheRecord,
    build_cache_index,
    canonical_cache_index_json,
    parse_cache_index_json,
    select_rebuilds,
)
from sideloadedipa.cache.fingerprint import SigningCacheFingerprint


def fingerprint(task_name: str, digest: str, *, schema: int = 1) -> SigningCacheFingerprint:
    return SigningCacheFingerprint(schema, task_name, (("task", task_name),), digest * 64)


def record(value: SigningCacheFingerprint) -> TaskCacheRecord:
    return TaskCacheRecord(
        value.task_name,
        value.schema_version,
        value.sha256,
        value.task_name[0].lower() * 64,
        "f" * 64,
        "e" * 64,
    )


def test_unchanged_tasks_hit_and_only_changed_task_rebuilds() -> None:
    first = fingerprint("First", "a")
    second = fingerprint("Second", "b")
    cached = build_cache_index((record(first), record(second)))

    decisions = select_rebuilds((replace(first, sha256="c" * 64), second), cached)

    assert [(value.task_name, value.rebuild, value.reason) for value in decisions] == [
        ("First", True, RebuildReason.FINGERPRINT_CHANGED),
        ("Second", False, RebuildReason.CACHE_HIT),
    ]
    assert decisions[1].cached_artifact_sha256 == "s" * 64


def test_first_run_rebuilds_only_missing_task() -> None:
    first = fingerprint("First", "a")
    second = fingerprint("Second", "b")

    decisions = select_rebuilds((first, second), build_cache_index((record(first),)))

    assert [value.reason for value in decisions] == [
        RebuildReason.CACHE_HIT,
        RebuildReason.FIRST_RUN,
    ]


@pytest.mark.parametrize("force", [False, True])
def test_schema_change_or_force_rebuilds_every_selected_task(force: bool) -> None:
    first = fingerprint("First", "a")
    second = fingerprint("Second", "b")
    cached = build_cache_index((record(first), record(second)))
    if not force:
        cached = build_cache_index(
            (replace(record(first), fingerprint_schema_version=0), record(second))
        )

    decisions = select_rebuilds((first, second), cached, force=force)

    expected = RebuildReason.FORCED if force else RebuildReason.SCHEMA_CHANGED
    assert all(value.rebuild and value.reason is expected for value in decisions)


def test_cache_index_is_canonical_and_detects_tampering() -> None:
    first = fingerprint("First", "a")
    second = fingerprint("Second", "b")
    index = build_cache_index((record(second), record(first)))
    document = json.loads(canonical_cache_index_json(index))

    assert document["schema_version"] == CACHE_INDEX_SCHEMA_VERSION
    assert [value["task_name"] for value in document["records"]] == ["First", "Second"]
    assert document["index_sha256"] == index.index_sha256
    assert parse_cache_index_json(canonical_cache_index_json(index)) == index
    with pytest.raises(ValueError, match="inconsistent"):
        canonical_cache_index_json(replace(index, index_sha256="0" * 64))

    document["records"][0]["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest does not match"):
        parse_cache_index_json(json.dumps(document).encode())


def test_duplicate_current_or_cached_tasks_are_rejected() -> None:
    first = fingerprint("First", "a")

    with pytest.raises(ValueError, match="duplicate"):
        select_rebuilds((first, first), None)
    with pytest.raises(ValueError, match="duplicate"):
        build_cache_index((record(first), record(first)))


def test_pending_verification_field_round_trips_but_cannot_be_a_cache_hit() -> None:
    current = fingerprint("First", "a")
    pending = replace(record(current), verification_report_sha256=None)
    index = build_cache_index((pending,))

    assert parse_cache_index_json(canonical_cache_index_json(index)) == index
    decision = select_rebuilds((current,), index)[0]
    assert decision.rebuild is True
    assert decision.reason is RebuildReason.CACHE_REJECTED
