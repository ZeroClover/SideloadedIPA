"""Read-only source resolution and IPA inventory use case."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    FrozenJsonObject,
    SourceKind,
    Task,
    TaskConfiguration,
    freeze_json,
    thaw_json,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode, SideloadedIPAError
from sideloadedipa.ipa import (
    canonical_graph_json,
    discover_bundle_graph,
    discover_bundle_structure,
    extract_ipa_safely,
)
from sideloadedipa.sources import (
    DownloadedSource,
    download_source_asset,
    fetch_github_release,
    github_repository_name,
    select_release_asset,
)
from sideloadedipa.workspace import TaskWorkspace, task_workspace


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
        timeout_seconds: float = 60,
        chunk_size: int = 1024 * 1024,
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


def _selected_tasks(configuration: TaskConfiguration, names: tuple[str, ...]) -> tuple[Task, ...]:
    if not names:
        return configuration.tasks
    if len(set(names)) != len(names):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "inspect task selection contains duplicates",
            remediation="pass each --task value once",
        )
    tasks = {task.task_name: task for task in configuration.tasks}
    missing = tuple(name for name in names if name not in tasks)
    if missing:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "inspect task selection contains unknown task names",
            remediation="select task names declared in the configuration",
            safe_details=(("task_names", missing),),
        )
    return tuple(tasks[name] for name in names)


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


def _resolve_source(
    task: Task, dependencies: InspectDependencies, token: str | None
) -> ResolvedSource:
    if task.source.kind is SourceKind.DIRECT_URL:
        asset_name = unquote(Path(urlsplit(task.source.location).path).name) or "source.ipa"
        return ResolvedSource(
            url=task.source.location,
            expected_sha256=None,
            advertised_size=None,
            evidence={
                "kind": task.source.kind.value,
                "repository": None,
                "release_tag": None,
                "asset_id": None,
                "asset_name": asset_name,
                "advertised_size": None,
                "advertised_sha256": None,
            },
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
            "asset_id": asset.asset_id,
            "asset_name": asset.name,
            "advertised_size": asset.size,
            "advertised_sha256": asset.digest.removeprefix("sha256:") if asset.digest else None,
        },
    )


def _diagnostic(error: SideloadedIPAError) -> dict[str, object]:
    value = error.to_diagnostic()
    return {
        "code": value.code,
        "message": value.message,
        "bundle_id": value.bundle_id,
        "remediation": value.remediation,
        "details": {key: thaw_json(item) for key, item in value.details},
    }


def _structure_document(nodes: tuple[BundleNode, ...]) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "nodes": [
            {
                "path": str(node.path),
                "kind": node.kind.value,
                "parent": str(node.parent_path) if node.parent_path else None,
                "depth": node.depth,
                "executable_path": str(node.executable_path),
                "executable_sha256": node.executable_sha256,
                "source_bundle_id": node.source_bundle_id,
                "info_plist_sha256": node.info_plist_sha256,
                "version": node.version,
                "short_version": node.short_version,
                "profile_bearing": node.profile_bearing,
            }
            for node in nodes
        ],
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    document["structure_sha256"] = hashlib.sha256(encoded).hexdigest()
    return document


def _inspect_task(
    task: Task,
    resolved: ResolvedSource,
    dependencies: InspectDependencies,
    workspace_root: Path,
) -> tuple[dict[str, object], str, bool]:
    with dependencies.workspace(workspace_root, task.task_name) as workspace:
        downloaded = dependencies.download(
            resolved.url,
            workspace.source_ipa,
            expected_sha256=resolved.expected_sha256,
        )
        if resolved.advertised_size is not None and downloaded.size != resolved.advertised_size:
            raise DomainError(
                ErrorCode.SOURCE_RELEASE_INVALID,
                "downloaded asset size differs from GitHub release evidence",
                remediation="retry after reviewing the current release asset",
                safe_details=(
                    ("advertised_size", resolved.advertised_size),
                    ("downloaded_size", downloaded.size),
                ),
            )
        entries = dependencies.extract(downloaded.path, workspace.extracted)
        structural_nodes = dependencies.discover_structure(workspace.extracted)
        try:
            graph = dependencies.discover(workspace.extracted, downloaded.sha256)
        except SideloadedIPAError as error:
            source = dict(resolved.evidence)
            source.update(
                {"downloaded_size": downloaded.size, "downloaded_sha256": downloaded.sha256}
            )
            report = {
                "task_name": task.task_name,
                "status": "failed",
                "source": source,
                "archive_entries": len(entries),
                "structure": _structure_document(structural_nodes),
                "inventory": None,
                "diagnostic": _diagnostic(error),
            }
            human = f"{task.task_name}: failed [{error.code.value}] {error.message}"
            return report, human, False

    source = dict(resolved.evidence)
    source.update({"downloaded_size": downloaded.size, "downloaded_sha256": downloaded.sha256})
    inventory = json.loads(canonical_graph_json(graph))
    profile_bundles = sum(node.profile_bearing for node in graph.nodes)
    report = {
        "task_name": task.task_name,
        "status": "passed",
        "source": source,
        "archive_entries": len(entries),
        "inventory": inventory,
    }
    human = (
        f"{task.task_name}: passed; {profile_bundles} profile bundle(s), "
        f"{len(graph.nodes)} code node(s), graph {graph.graph_sha256[:12]}"
    )
    return report, human, True


def inspect_command(
    request: CommandRequest,
    dependencies: InspectDependencies = InspectDependencies(),
) -> CommandResult:
    """Inspect selected tasks without signing or mutating remote state."""

    configuration = dependencies.load(request.config_path)
    tasks = _selected_tasks(configuration, request.task_names)
    workspace_root = Path(tempfile.gettempdir()) / "sideloadedipa-inspect"
    token = os.getenv("GITHUB_TOKEN")
    reports: list[dict[str, object]] = []
    human_lines: list[str] = []
    failed = 0
    for task in tasks:
        resolved: ResolvedSource | None = None
        try:
            resolved = _resolve_source(task, dependencies, token)
            report, human, passed = _inspect_task(task, resolved, dependencies, workspace_root)
            if not passed:
                failed += 1
        except SideloadedIPAError as error:
            failed += 1
            report = {
                "task_name": task.task_name,
                "status": "failed",
                "source": dict(resolved.evidence) if resolved is not None else None,
                "diagnostic": _diagnostic(error),
            }
            human = f"{task.task_name}: failed [{error.code.value}] {error.message}"
        reports.append(report)
        human_lines.append(human)

    document = {
        "schema_version": 1,
        "command": "inspect",
        "status": "failed" if failed else "passed",
        "task_count": len(tasks),
        "succeeded": len(tasks) - failed,
        "failed": failed,
        "tasks": reports,
    }
    frozen = freeze_json(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("inspect report root must be an object")
    summary = f"Inspection: {len(tasks) - failed} passed, {failed} failed"
    return CommandResult(
        exit_code=1 if failed else 0,
        human_output="\n".join((summary, *human_lines)),
        payload=frozen.items,
    )
