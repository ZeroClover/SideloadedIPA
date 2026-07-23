"""Protocol boundaries for pipeline side effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from sideloadedipa.domain import (
    CertificateMaterial,
    PublicationCandidate,
    SigningPlan,
    SigningResult,
    StoredArtifact,
    VerificationResult,
)


@runtime_checkable
class SigningBackend(Protocol):
    def sign(
        self,
        plan: SigningPlan,
        source_ipa: Path,
        output_ipa: Path,
        certificate: CertificateMaterial,
    ) -> SigningResult: ...


@runtime_checkable
class Verifier(Protocol):
    def verify(self, plan: SigningPlan, signed_ipa: Path) -> VerificationResult: ...


@runtime_checkable
class VerifiedPublicationGateway(Protocol):
    def read_registry(self) -> Mapping[str, object] | None: ...

    def upload_artifact(self, candidate: PublicationCandidate) -> StoredArtifact: ...

    def publish_registry(self, document: Mapping[str, object]) -> tuple[str, str]: ...

    def restore_registry(self, document: Mapping[str, object] | None) -> None: ...

    def delete_uploaded(self, keys: Sequence[str]) -> None: ...

    def revalidate(self) -> None: ...

    def object_key_from_url(self, url: str) -> str | None: ...

    def cleanup_stale(
        self,
        slugs: Sequence[str],
        referenced_keys: frozenset[str],
    ) -> tuple[str, ...]: ...
