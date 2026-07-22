"""R2 and registry adapter for verified publication."""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from sideloadedipa.domain import PublicationCandidate, StoredArtifact
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.legacy.r2_store import R2Store
from sideloadedipa.retrying import RetryOperation, RetryPolicy, retry_call


def _transient_r2_error(error: Exception) -> bool:
    if isinstance(error, OSError):
        return True
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    metadata = response.get("ResponseMetadata", {})
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    return isinstance(status, int) and (status in {408, 429} or status >= 500)


class R2PublicationGateway:
    def __init__(
        self,
        store: R2Store,
        revalidate: Callable[[], bool],
        *,
        retry_policy: RetryPolicy = RetryPolicy(),
        sleep: Callable[[float], None] = time.sleep,
        random_unit: Callable[[], float] = random.random,
    ) -> None:
        self._store = store
        self._trigger_revalidate = revalidate
        self._retry_policy = retry_policy
        self._sleep = sleep
        self._random_unit = random_unit

    def _retry(
        self,
        operation_id: str,
        operation: RetryOperation,
        action: Callable[[], object],
    ) -> object:
        return retry_call(
            operation_id=operation_id,
            operation=operation,
            action=action,
            is_transient=_transient_r2_error,
            policy=self._retry_policy,
            sleep=self._sleep,
            random_unit=self._random_unit,
        )

    def read_registry(self) -> Mapping[str, object] | None:
        return cast(
            Mapping[str, object] | None,
            self._retry(
                f"registry:{self._store.apps_json_key}:read",
                RetryOperation.READ,
                lambda: self._store.download_json(self._store.apps_json_key),
            ),
        )

    def upload_artifact(self, candidate: PublicationCandidate) -> StoredArtifact:
        path = Path(candidate.artifact_path)
        immutable_filename = f"{candidate.artifact_sha256[:12]}-{candidate.filename}"
        key = self._store.ipa_key(candidate.slug, candidate.version, immutable_filename)

        def upload_and_confirm() -> tuple[str, str]:
            url = self._store.upload_ipa(path, key)
            stored_sha256 = hashlib.sha256(self._store.download_bytes(key)).hexdigest()
            return url, stored_sha256

        url, stored_sha256 = cast(
            tuple[str, str],
            self._retry(
                f"artifact:{key}:{candidate.artifact_sha256}",
                RetryOperation.CONTENT_ADDRESSED_UPLOAD,
                upload_and_confirm,
            ),
        )
        return StoredArtifact(key, url, stored_sha256, path.stat().st_size)

    def publish_registry(self, document: Mapping[str, object]) -> tuple[str, str]:
        payload = dict(document)
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        digest = hashlib.sha256(body).hexdigest()
        self._retry(
            f"registry:{self._store.apps_json_key}:{digest}",
            RetryOperation.REGISTRY_REPLACE,
            lambda: self._store.upload_json(self._store.apps_json_key, payload),
        )
        return self._store.apps_json_key, digest

    def restore_registry(self, document: Mapping[str, object] | None) -> None:
        if document is None:
            self._store.delete_keys([self._store.apps_json_key])
        else:
            payload = dict(document)
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            digest = hashlib.sha256(body).hexdigest()
            self._retry(
                f"registry:{self._store.apps_json_key}:{digest}",
                RetryOperation.REGISTRY_REPLACE,
                lambda: self._store.upload_json(self._store.apps_json_key, payload),
            )

    def delete_uploaded(self, keys: Sequence[str]) -> None:
        if keys:
            self._store.delete_keys(list(keys))

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
