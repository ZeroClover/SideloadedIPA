"""Tests for the existing R2 implementation behind the publication gateway."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from sideloadedipa.adapters.publication import R2PublicationGateway
from sideloadedipa.adapters.publication.r2_store import R2Store
from sideloadedipa.errors import DomainError, ErrorCode
from tests.conftest import publication_candidate as candidate


@dataclass
class FakeR2Store:
    apps_json_key: str = "site/apps.json"
    public_base_url: str = "https://cdn.example"
    objects: dict[str, bytes] = field(default_factory=dict)
    registry: dict[str, object] | None = None
    failures_remaining: int = 0
    registry_failures_remaining: int = 0
    upload_attempts: int = 0
    registry_attempts: list[dict[str, object]] = field(default_factory=list)

    def download_json(self, key: str) -> dict[str, object] | None:
        assert key == self.apps_json_key
        return self.registry

    def ipa_key(self, slug: str, version: str, filename: str) -> str:
        return f"apps/{slug}/{version}/{filename}"

    def upload_ipa(self, path: Path, key: str) -> str:
        self.upload_attempts += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise OSError("transient upload")
        self.objects[key] = path.read_bytes()
        return f"{self.public_base_url}/{key}"

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def upload_json(self, key: str, payload: dict[str, object]) -> str:
        assert key == self.apps_json_key
        self.registry_attempts.append(payload)
        if self.registry_failures_remaining:
            self.registry_failures_remaining -= 1
            raise OSError("transient registry write")
        self.registry = payload
        return f"{self.public_base_url}/{key}"

    def delete_keys(self, keys: list[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)

    def key_from_url(self, url: str) -> str | None:
        prefix = f"{self.public_base_url}/"
        return url.removeprefix(prefix) if url.startswith(prefix) else None

    def cleanup_stale(self, slugs: list[str], referenced: set[str]) -> list[str]:
        assert slugs == ["example"]
        assert referenced == {"apps/example/1.2.3/Example.ipa"}
        return ["apps/example/1.0/Example.ipa"]


def gateway(store: FakeR2Store, *, revalidated: bool = True) -> R2PublicationGateway:
    return R2PublicationGateway(
        cast(R2Store, store),
        lambda: revalidated,
        sleep=lambda delay: None,
        random_unit=lambda: 0.5,
    )


def test_adapter_uploads_and_confirms_artifact_through_r2_api(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    value = candidate(artifact)

    stored = gateway(FakeR2Store()).upload_artifact(value)

    assert stored.key == f"apps/example/1.2.3/{value.artifact_sha256[:12]}-Example.ipa"
    assert stored.sha256 == hashlib.sha256(b"verified").hexdigest()
    assert stored.size == len(b"verified")


def test_content_addressed_upload_retries_with_same_key(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    store = FakeR2Store(failures_remaining=2)

    stored = gateway(store).upload_artifact(candidate(artifact))

    assert store.upload_attempts == 3
    assert tuple(store.objects) == (stored.key,)


def test_adapter_delegates_registry_revalidation_and_cleanup() -> None:
    store = FakeR2Store()
    adapter = gateway(store)
    ipa_url = "https://cdn.example/apps/example/1.2.3/Example.ipa"
    document = {
        "updatedAt": "2026-07-21T00:00:00Z",
        "apps": [
            {
                "slug": "example",
                "ipaUrl": ipa_url,
            }
        ],
    }

    key, digest = adapter.publish_registry(document)
    adapter.revalidate()
    removed = adapter.cleanup_stale(["example"], frozenset({"apps/example/1.2.3/Example.ipa"}))

    assert key == "site/apps.json"
    assert len(digest) == 64
    assert store.registry == document
    assert adapter.object_key_from_url(ipa_url) == "apps/example/1.2.3/Example.ipa"
    assert removed == ("apps/example/1.0/Example.ipa",)

    adapter.restore_registry({"apps": []})
    assert store.registry == {"apps": []}
    store.objects["new"] = b"value"
    adapter.delete_uploaded(("new",))
    assert "new" not in store.objects


def test_registry_retry_preserves_the_exact_document() -> None:
    store = FakeR2Store(registry_failures_remaining=2)
    document: dict[str, object] = {"updatedAt": "2026-07-21T00:00:00Z", "apps": []}

    gateway(store).publish_registry(document)

    assert store.registry_attempts == [document, document, document]


def test_adapter_turns_revalidation_rejection_into_domain_failure() -> None:
    with pytest.raises(DomainError) as caught:
        gateway(FakeR2Store(), revalidated=False).revalidate()

    assert caught.value.code is ErrorCode.PUBLICATION_FAILED
