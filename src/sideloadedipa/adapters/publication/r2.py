"""R2 and registry adapter for verified publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from sideloadedipa.domain import PublicationCandidate, StoredArtifact
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.legacy.r2_store import R2Store


class R2PublicationGateway:
    def __init__(self, store: R2Store, revalidate: Callable[[], bool]) -> None:
        self._store = store
        self._trigger_revalidate = revalidate

    def read_registry(self) -> Mapping[str, object] | None:
        return cast(
            Mapping[str, object] | None, self._store.download_json(self._store.apps_json_key)
        )

    def upload_artifact(self, candidate: PublicationCandidate) -> StoredArtifact:
        path = Path(candidate.artifact_path)
        immutable_filename = f"{candidate.artifact_sha256[:12]}-{candidate.filename}"
        key = self._store.ipa_key(candidate.slug, candidate.version, immutable_filename)
        url = self._store.upload_ipa(path, key)
        stored_sha256 = hashlib.sha256(self._store.download_bytes(key)).hexdigest()
        return StoredArtifact(key, url, stored_sha256, path.stat().st_size)

    def publish_registry(self, document: Mapping[str, object]) -> tuple[str, str]:
        payload = dict(document)
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self._store.upload_json(self._store.apps_json_key, payload)
        return self._store.apps_json_key, hashlib.sha256(body).hexdigest()

    def restore_registry(self, document: Mapping[str, object] | None) -> None:
        if document is None:
            self._store.delete_keys([self._store.apps_json_key])
        else:
            self._store.upload_json(self._store.apps_json_key, dict(document))

    def revalidate(self) -> None:
        if not self._trigger_revalidate():
            raise DomainError(
                ErrorCode.PUBLICATION_FAILED,
                "Vercel revalidation did not complete",
                remediation="retain the committed registry and retry revalidation before cleanup",
            )

    def object_key_from_url(self, url: str) -> str | None:
        return self._store.key_from_url(url)

    def cleanup_stale(
        self,
        slugs: Sequence[str],
        referenced_keys: frozenset[str],
    ) -> tuple[str, ...]:
        return tuple(self._store.cleanup_stale(list(slugs), set(referenced_keys)))
