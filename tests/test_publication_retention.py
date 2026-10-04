"""Publication boundaries protect both active and temporarily exposed links."""

from pathlib import Path

import pytest

from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.pipeline.publication_service import VerifiedPublicationService
from tests.test_publication import NOW, RecordingGateway, candidate


def test_previous_references_are_protected_during_promotion(tmp_path: Path) -> None:
    artifact = tmp_path / "App.ipa"
    artifact.write_bytes(b"new")
    old = "apps/example/old/Example.ipa"

    class Gateway(RecordingGateway):
        def read_registry(self) -> dict[str, object]:
            return {"apps": [{"slug": "example", "ipaUrl": f"https://cdn.example/{old}"}]}

        def cleanup_stale(self, slugs: object, referenced_keys: frozenset[str]) -> tuple[str, ...]:
            assert old in referenced_keys
            assert "apps/example/1.2.3/Example.ipa" in referenced_keys
            return ()

    VerifiedPublicationService(Gateway()).publish((candidate(artifact),), now=NOW)


def test_ambiguous_registry_write_rolls_back_but_keeps_advertised_uploads(tmp_path: Path) -> None:
    artifact = tmp_path / "App.ipa"
    artifact.write_bytes(b"new")

    class Gateway(RecordingGateway):
        def publish_registry(self, document: object) -> tuple[str, str]:
            super().publish_registry(document)
            raise OSError("response lost after successful write")

    gateway = Gateway()
    with pytest.raises(DomainError, match="previous snapshot was restored"):
        VerifiedPublicationService(gateway).publish((candidate(artifact),), now=NOW)
    assert gateway.calls == ["read", "upload", "registry", "restore", "revalidate"]
    assert gateway.published is not None
    assert gateway.deleted_uploaded == ()


def test_rollback_failure_does_not_delete_new_registry_references(tmp_path: Path) -> None:
    artifact = tmp_path / "App.ipa"
    artifact.write_bytes(b"new")

    class Gateway(RecordingGateway):
        def restore_registry(self, document: object) -> None:
            raise OSError("rollback unavailable")

    gateway = Gateway(fail_at="revalidate")
    with pytest.raises(DomainError, match="rollback both failed"):
        VerifiedPublicationService(gateway).publish((candidate(artifact),), now=NOW)
    assert "delete-uploaded" not in gateway.calls
    assert "cleanup" not in gateway.calls


def test_successful_rollback_cache_expiry_preserves_original_failure(tmp_path: Path) -> None:
    artifact = tmp_path / "App.ipa"
    artifact.write_bytes(b"new")

    class Gateway(RecordingGateway):
        def revalidate(self) -> None:
            self.calls.append("revalidate")
            if self.calls.count("revalidate") == 1:
                raise DomainError(ErrorCode.PUBLICATION_FAILED, "initial refresh failed")

    gateway = Gateway()
    with pytest.raises(DomainError, match="initial refresh failed"):
        VerifiedPublicationService(gateway).publish((candidate(artifact),), now=NOW)
    assert gateway.calls[-2:] == ["restore", "revalidate"]
    assert not gateway.deleted_uploaded
