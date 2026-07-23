"""Read-only source resolution dependencies used by production inventory."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

from sideloadedipa.config import load_configuration
from sideloadedipa.domain import BundleGraph, BundleNode, SourceKind, Task, TaskConfiguration
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.ipa import discover_bundle_graph, discover_bundle_structure, extract_ipa_safely
from sideloadedipa.sources import (
    DownloadedSource,
    download_source_asset,
    fetch_github_release,
    github_repository_name,
    select_release_asset,
)
from sideloadedipa.util.workspace import TaskWorkspace, task_workspace


class ConfigurationLoader(Protocol):
    def __call__(self, path: Path) -> TaskConfiguration: ...


class ReleaseFetcher(Protocol):
    def __call__(
        self,
        repository_url: str,
        *,
        use_prerelease: bool = False,
        token: str | None = None,
        timeout_seconds: float = 30,
    ) -> Mapping[str, object]: ...


class SourceDownloader(Protocol):
    def __call__(
        self,
        url: str,
        destination: Path,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
    ) -> DownloadedSource: ...


class ArchiveExtractor(Protocol):
    def __call__(self, ipa_path: Path, destination: Path) -> tuple[object, ...]: ...


class GraphDiscoverer(Protocol):
    def __call__(self, extracted_root: Path, source_sha256: str) -> BundleGraph: ...


class StructureDiscoverer(Protocol):
    def __call__(self, extracted_root: Path) -> tuple[BundleNode, ...]: ...


class WorkspaceFactory(Protocol):
    def __call__(
        self, base_directory: Path, task_name: str
    ) -> AbstractContextManager[TaskWorkspace]: ...


@dataclass(frozen=True, slots=True)
class InspectDependencies:
    load: ConfigurationLoader = load_configuration
    fetch_release: ReleaseFetcher = fetch_github_release
    download: SourceDownloader = download_source_asset
    extract: ArchiveExtractor = extract_ipa_safely
    discover_structure: StructureDiscoverer = discover_bundle_structure
    discover: GraphDiscoverer = discover_bundle_graph
    workspace: WorkspaceFactory = task_workspace


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    url: str
    expected_sha256: str | None
    evidence: Mapping[str, object]
    advertised_size: int | None


def _release_tag(release: Mapping[str, object]) -> str:
    value = release.get("tag_name")
    if not isinstance(value, str) or not value:
        raise DomainError(
            ErrorCode.SOURCE_RELEASE_INVALID,
            "release tag must be a non-empty string",
            remediation="retry with an unmodified GitHub release API response",
            safe_details=(("field", "tag_name"),),
        )
    return value


def resolve_source(
    task: Task, dependencies: InspectDependencies, token: str | None
) -> ResolvedSource:
    if task.source.kind is SourceKind.DIRECT_URL:
        if task.source.ipa_sha256 is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "direct source is missing its reviewed SHA-256",
                task_name=task.task_name,
                remediation=("run 'shasum -a 256 <path-to-ipa>' and add the digest as ipa_sha256"),
                safe_details=(("field", "ipa_sha256"),),
            )
        asset_name = unquote(Path(urlsplit(task.source.location).path).name) or "source.ipa"
        return ResolvedSource(
            url=task.source.location,
            expected_sha256=task.source.ipa_sha256,
            advertised_size=None,
            evidence={
                "kind": task.source.kind.value,
                "repository": None,
                "release_tag": None,
                "published_at": None,
                "asset_id": None,
                "asset_name": asset_name,
                "advertised_size": None,
                "advertised_sha256": task.source.ipa_sha256,
                "configured_sha256": task.source.ipa_sha256,
            },
        )

    if task.source.ipa_sha256 is not None:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "GitHub release source cannot declare a direct-source digest",
            task_name=task.task_name,
            safe_details=(("field", "ipa_sha256"),),
        )

    release = dependencies.fetch_release(
        task.source.location,
        use_prerelease=task.source.use_prerelease,
        token=token,
    )
    asset = select_release_asset(release, task.source.release_glob or "*.ipa")
    return ResolvedSource(
        url=asset.browser_download_url,
        expected_sha256=asset.digest,
        advertised_size=asset.size,
        evidence={
            "kind": task.source.kind.value,
            "repository": github_repository_name(task.source.location),
            "release_tag": _release_tag(release),
            "published_at": release.get("published_at"),
            "asset_id": asset.asset_id,
            "asset_name": asset.name,
            "advertised_size": asset.size,
            "advertised_sha256": asset.digest.removeprefix("sha256:") if asset.digest else None,
        },
    )
