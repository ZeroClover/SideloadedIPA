"""Protocol boundaries for pipeline side effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from sideloadedipa.domain import (
    AppleResource,
    AppleResourcePlan,
    BundleGraph,
    CertificateMaterial,
    PublicationCandidate,
    PublicationResult,
    SigningPlan,
    SigningResult,
    SourceAsset,
    StoredArtifact,
    Task,
    VerificationResult,
)


@runtime_checkable
class SourceRepository(Protocol):
    def fetch(self, task: Task, destination: Path) -> SourceAsset: ...


@runtime_checkable
class ArchiveInspector(Protocol):
    def inspect(self, source: SourceAsset, workspace: Path) -> BundleGraph: ...


@runtime_checkable
class AppleDeveloperClient(Protocol):
    def collect_state(self) -> tuple[AppleResource, ...]: ...

    def apply(self, plan: AppleResourcePlan) -> tuple[AppleResource, ...]: ...


@runtime_checkable
class CertificateProvider(Protocol):
    def load(self, workspace: Path) -> CertificateMaterial: ...


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
class ArtifactStore(Protocol):
    def put(self, task: Task, verified_ipa: Path, sha256: str) -> StoredArtifact: ...


@runtime_checkable
class RegistryPublisher(Protocol):
    def publish(
        self,
        artifacts: Sequence[StoredArtifact],
    ) -> tuple[PublicationResult, ...]: ...


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


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class Filesystem(Protocol):
    def temporary_directory(self, prefix: str) -> AbstractContextManager[Path]: ...

    def copy_file(self, source: Path, destination: Path) -> None: ...

    def atomic_replace(self, source: Path, destination: Path) -> None: ...

    def remove_tree(self, path: Path) -> None: ...
