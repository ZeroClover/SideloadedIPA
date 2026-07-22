"""Atomic storage for digest-verified production signing-cache metadata."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.cache.decisions import (
    CacheIndex,
    canonical_cache_index_json,
    parse_cache_index_json,
)
from sideloadedipa.util.atomics import atomic_write_bytes


@dataclass(frozen=True, slots=True)
class SigningCacheStore:
    root: Path

    @property
    def index_path(self) -> Path:
        return self.root / "signing-index.json"

    def artifact_path(self, task_name: str, fingerprint_sha256: str) -> Path:
        if len(fingerprint_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint_sha256
        ):
            raise ValueError("cache fingerprint is not a lowercase SHA-256 digest")
        task_digest = hashlib.sha256(task_name.encode()).hexdigest()[:16]
        return self.root / "signed-artifacts" / task_digest / f"{fingerprint_sha256}.ipa"

    def signing_report_path(self, task_name: str, fingerprint_sha256: str) -> Path:
        artifact = self.artifact_path(task_name, fingerprint_sha256)
        return artifact.with_suffix(".signing-report.json")

    def load(self) -> CacheIndex | None:
        if not self.index_path.exists():
            return None
        return parse_cache_index_json(self.index_path.read_bytes())

    def save(self, index: CacheIndex) -> None:
        payload = canonical_cache_index_json(index) + b"\n"
        atomic_write_bytes(self.index_path, payload)
