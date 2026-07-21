"""Tests for the verified publication transaction ordering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    PublicationCandidate,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    StoredArtifact,
    VerificationFinding,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.publication import VerifiedPublicationService
from sideloadedipa.verification import build_verification_result, required_verification_checks

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def signing_plan() -> SigningPlan:
    values = normalize_entitlements({"application-identifier": "TEAM.io.example.app"})
    return SigningPlan(
        "Example",
        "0" * 64,
        "1" * 64,
        "2" * 64,
        SigningBackendIdentity("fixture", "1", "3" * 64, "1"),
        (
            SigningNodePlan(
                PurePosixPath("Payload/App.app"),
                PurePosixPath("Payload/App.app/App"),
                BundleNodeKind.APP,
                0,
                "io.example.app",
                "PROFILE",
                PurePosixPath("Example/profile.mobileprovision"),
                "4" * 64,
                values.values,
                values.sha256,
            ),
        ),
        "a" * 64,
    )


def candidate(artifact: Path) -> PublicationCandidate:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    plan = signing_plan()
    verification = build_verification_result(
        plan,
        digest,
        tuple(
            VerificationFinding(path, check.replace("*", "arm64"), True)
            for path, check in required_verification_checks(plan)
        ),
    )
    return PublicationCandidate(
        "Example",
        "example",
        "Example",
        "io.example.app",
        "1.2.3",
        "Example.ipa",
        str(artifact),
        digest,
        "https://cdn.example/apps/example/icon.png",
        plan,
        verification,
    )


@dataclass
class RecordingGateway:
    calls: list[str] = field(default_factory=list)
    published: dict[str, object] | None = None

    def read_registry(self) -> dict[str, object]:
        self.calls.append("read")
        return {
            "updatedAt": "2026-01-01T00:00:00Z",
            "apps": [
                {
                    "slug": "other",
                    "ipaUrl": "https://cdn.example/apps/other/1/Other.ipa",
                }
            ],
        }

    def upload_artifact(self, value: PublicationCandidate) -> StoredArtifact:
        self.calls.append("upload")
        return StoredArtifact(
            f"apps/{value.slug}/{value.version}/{value.filename}",
            f"https://cdn.example/apps/{value.slug}/{value.version}/{value.filename}",
            value.artifact_sha256,
            Path(value.artifact_path).stat().st_size,
        )

    def publish_registry(self, document: object) -> tuple[str, str]:
        self.calls.append("registry")
        assert isinstance(document, dict)
        self.published = document
        return "site/apps.json", "c" * 64

    def revalidate(self) -> None:
        self.calls.append("revalidate")

    def object_key_from_url(self, url: str) -> str | None:
        prefix = "https://cdn.example/"
        return url.removeprefix(prefix) if url.startswith(prefix) else None

    def cleanup_stale(self, slugs: object, referenced_keys: frozenset[str]) -> tuple[str, ...]:
        self.calls.append("cleanup")
        assert slugs == ["example"]
        assert "apps/example/1.2.3/Example.ipa" in referenced_keys
        assert "apps/other/1/Other.ipa" in referenced_keys
        return ("apps/example/1.0/Example.ipa",)


def test_verified_publication_uses_strict_atomic_order(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    gateway = RecordingGateway()

    result = VerifiedPublicationService(gateway).publish((candidate(artifact),), now=NOW)

    assert gateway.calls == ["read", "upload", "registry", "revalidate", "cleanup"]
    assert result[0].registry_key == "site/apps.json"
    assert result[0].stale_keys_removed == ("apps/example/1.0/Example.ipa",)
    assert gateway.published is not None
    apps = gateway.published["apps"]
    assert isinstance(apps, list)
    assert [app["slug"] for app in apps] == ["other", "example"]


@pytest.mark.parametrize("failure", ["gate", "plan", "artifact"])
def test_invalid_candidate_blocks_every_publication_side_effect(
    tmp_path: Path, failure: str
) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    value = candidate(artifact)
    if failure == "gate":
        value = replace(value, verification=replace(value.verification, passed=False))
    elif failure == "plan":
        value = replace(value, plan=replace(value.plan, plan_sha256="d" * 64))
    else:
        artifact.write_bytes(b"tampered")
    gateway = RecordingGateway()

    with pytest.raises(DomainError) as caught:
        VerifiedPublicationService(gateway).publish((value,), now=NOW)

    assert caught.value.code is ErrorCode.PUBLICATION_FAILED
    assert gateway.calls == []


def test_uploaded_digest_mismatch_stops_before_registry_mutation(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    gateway = RecordingGateway()
    original = gateway.upload_artifact

    def mismatched(value: PublicationCandidate) -> StoredArtifact:
        return replace(original(value), sha256="0" * 64)

    gateway.upload_artifact = mismatched  # type: ignore[method-assign]

    with pytest.raises(DomainError, match="digest"):
        VerifiedPublicationService(gateway).publish((candidate(artifact),), now=NOW)

    assert gateway.calls == ["read", "upload"]
