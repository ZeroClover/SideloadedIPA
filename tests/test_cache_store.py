"""Tests for production signing-cache persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from sideloadedipa.cache_decisions import TaskCacheRecord, build_cache_index
from sideloadedipa.cache_store import SigningCacheStore


def test_cache_store_round_trips_digest_verified_index(tmp_path: Path) -> None:
    store = SigningCacheStore(tmp_path)
    index = build_cache_index((TaskCacheRecord("Task", 1, "a" * 64, "b" * 64, "c" * 64),))

    assert store.load() is None
    store.save(index)

    assert store.load() == index
    assert store.artifact_path("Task", "a" * 64).is_relative_to(tmp_path)


def test_cache_store_rejects_invalid_artifact_identity_and_index(tmp_path: Path) -> None:
    store = SigningCacheStore(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        store.artifact_path("Task", "../escape")
    tmp_path.mkdir(exist_ok=True)
    store.index_path.write_text("{}")
    with pytest.raises(ValueError, match="digest does not match"):
        store.load()
