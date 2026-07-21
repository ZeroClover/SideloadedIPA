"""Tests for the existing R2 implementation behind the publication gateway."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sideloadedipa.adapters.publication import R2PublicationGateway
from sideloadedipa.domain import PublicationCandidate
from sideloadedipa.errors import DomainError, ErrorCode
from tests.test_publication import candidate


@dataclass
class FakeR2Store:
    apps_json_key: str = "site/apps.json"
    public_base_url: str = "https://cdn.example"
    objects: dict[str, bytes] = field(default_factory=dict)
    registry: dict[str, object] | None = None

    def download_json(self, key: str) -> dict[str, object] | None:
        assert key == self.apps_json_key
        return self.registry

    def ipa_key(self, slug: str, version: str, filename: str) -> str:
        return f"apps/{slug}/{version}/{filename}"

    def upload_ipa(self, path: Path, key: str) -> str:
        self.objects[key] = path.read_bytes()
        return f"{self.public_base_url}/{key}"

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def upload_json(self, key: str, payload: dict[str, object]) -> str:
        assert key == self.apps_json_key
        self.registry = payload
        return f"{self.public_base_url}/{key}"

    def key_from_url(self, url: str) -> str | None:
        prefix = f"{self.public_base_url}/"
        return url.removeprefix(prefix) if url.startswith(prefix) else None

    def cleanup_stale(self, slugs: list[str], referenced: set[str]) -> list[str]:
        assert slugs == ["example"]
        assert referenced == {"apps/example/1.2.3/Example.ipa"}
        return ["apps/example/1.0/Example.ipa"]


def gateway(store: FakeR2Store, *, revalidated: bool = True) -> R2PublicationGateway:
    return R2PublicationGateway(store, lambda: revalidated)  # type: ignore[arg-type]


def test_adapter_uploads_and_confirms_artifact_through_r2_api(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    value = candidate(artifact)

    stored = gateway(FakeR2Store()).upload_artifact(value)

    assert stored.key == "apps/example/1.2.3/Example.ipa"
    assert stored.sha256 == hashlib.sha256(b"verified").hexdigest()
    assert stored.size == len(b"verified")


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


def test_adapter_turns_revalidation_rejection_into_domain_failure() -> None:
    with pytest.raises(DomainError) as caught:
        gateway(FakeR2Store(), revalidated=False).revalidate()

    assert caught.value.code is ErrorCode.PUBLICATION_FAILED
