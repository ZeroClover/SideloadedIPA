"""Canonical source intake, unsigned inventory, and policy transaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from sideloadedipa.application import CommandRequest
from sideloadedipa.domain.config import Task
from sideloadedipa.domain.pipeline import PipelineStage, SourceAsset, StageManifest
from sideloadedipa.errors import DomainError, ErrorCode, SideloadedIPAError
from sideloadedipa.pipeline.environment import PipelineEnvironmentDependencies
from sideloadedipa.pipeline.input_manifests import CanonicalInputManifestStore
from sideloadedipa.pipeline.inspection import ResolvedSource, resolve_source
from sideloadedipa.pipeline.package_runner import inspect_source_graph
from sideloadedipa.pipeline.sign_stage import json_digest, policy_sha256
from sideloadedipa.pipeline.source_state import (
    bind_download_evidence,
    read_source_selection,
    source_asset,
    validate_downloaded_source,
    write_source_selection,
)
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import SourceContext
from sideloadedipa.signing.preflight import validate_signing_preflight
from sideloadedipa.sources.download import DownloadedSource
from sideloadedipa.util.atomics import file_sha256

SourceResolver = Callable[
    [CommandRequest, Task],
    tuple[ResolvedSource, DownloadedSource, SourceAsset],
]


@dataclass(frozen=True, slots=True)
class SourceInventoryStage:
    package: PipelineEnvironmentDependencies
    evidence: StageEvidence

    def inputs(self, request: CommandRequest) -> CanonicalInputManifestStore:
        return CanonicalInputManifestStore(self.evidence.store(request.run_id))

    def source_path(self, request: CommandRequest, task: Task) -> Path:
        return self.evidence.store(request.run_id).task_root(task.task_name) / "source.ipa"

    def selection_path(self, request: CommandRequest, task: Task) -> Path:
        return self.evidence.store(request.run_id).task_root(task.task_name) / (
            "source-selection.json"
        )

    def resolve_source_asset(
        self,
        request: CommandRequest,
        task: Task,
    ) -> tuple[ResolvedSource, DownloadedSource, SourceAsset]:
        dependencies = self.package.inspect
        path = self.source_path(request, task)
        selection_path = self.selection_path(request, task)
        if path.exists():
            resolved = read_source_selection(selection_path)
            digest = file_sha256(path)
            attempts = resolved.evidence.get("download_attempts", 1)
            downloaded = DownloadedSource(
                path,
                path.stat().st_size,
                digest,
                attempts if isinstance(attempts, int) else 1,
            )
            validate_downloaded_source(resolved, downloaded)
        else:
            resolved = resolve_source(
                task,
                dependencies,
                self.package.environment.get("GITHUB_TOKEN"),
            )
            downloaded = dependencies.download(
                resolved.url,
                path,
                expected_sha256=resolved.expected_sha256,
                expected_size=resolved.advertised_size,
            )
            resolved = bind_download_evidence(resolved, downloaded)
            write_source_selection(selection_path, resolved)
        return resolved, downloaded, source_asset(resolved, downloaded)

    def resolve(self, request: CommandRequest, task: Task) -> SourceContext:
        source_started_at = self.evidence.clock()
        resolved, downloaded, source = self.resolve_source_asset(request, task)
        source_completed_at = self.evidence.clock()
        inventory_started_at = self.evidence.clock()
        graph = inspect_source_graph(downloaded.path, task=task)
        inventory_completed_at = self.evidence.clock()
        return SourceContext(
            task,
            resolved,
            downloaded,
            source,
            graph,
            source_started_at,
            source_completed_at,
            inventory_started_at,
            inventory_completed_at,
        )

    def load_context(self, request: CommandRequest, task: Task) -> SourceContext:
        store = self.evidence.store(request.run_id)
        inputs = self.inputs(request).load(task)
        source_manifest = self.evidence.require(store, task.task_name, PipelineStage.SOURCE)
        inventory_manifest = self.evidence.require(store, task.task_name, PipelineStage.INVENTORY)
        policy_manifest = self.evidence.require(store, task.task_name, PipelineStage.POLICY)
        expected_policy = json_digest(
            {"policy": policy_sha256(task), "graph": inputs.graph.graph_sha256}
        )
        if (
            policy_manifest.result_sha256 != expected_policy
            or policy_manifest.predecessor_sha256 != inventory_manifest.manifest_sha256
        ):
            raise DomainError(
                ErrorCode.PIPELINE_TRANSITION_INVALID,
                "canonical policy evidence differs from current task inputs",
                task_name=task.task_name,
                remediation="use a new run ID after changing the task policy or source inputs",
                safe_details=(("stage", PipelineStage.POLICY.value),),
            )
        return SourceContext(
            task,
            inputs.resolved,
            inputs.downloaded,
            inputs.source,
            inputs.graph,
            source_manifest.started_at,
            source_manifest.completed_at,
            inventory_manifest.started_at,
            inventory_manifest.completed_at,
        )

    def load(
        self,
        request: CommandRequest,
        tasks: tuple[Task, ...],
    ) -> tuple[SourceContext, ...]:
        return tuple(self.load_context(request, task) for task in tasks)

    def inspect(
        self,
        request: CommandRequest,
        tasks: tuple[Task, ...],
        resolve_asset: SourceResolver | None = None,
    ) -> tuple[SourceContext, ...]:
        store = self.evidence.store(request.run_id)
        inputs = self.inputs(request)
        contexts: list[SourceContext] = []
        diagnostics: list[str] = []
        repository_root = request.config_path.resolve().parent.parent
        resolver = resolve_asset or self.resolve_source_asset
        for task in tasks:
            task_started_at = self.evidence.clock()
            source_manifest: StageManifest | None = None
            inventory_manifest: StageManifest | None = None
            try:
                source_manifest = store.load(task.task_name, PipelineStage.SOURCE)
                inventory_manifest = store.load(task.task_name, PipelineStage.INVENTORY)
                if inventory_manifest is not None:
                    canonical = inputs.load(task)
                    resolved = canonical.resolved
                    downloaded = canonical.downloaded
                    source = canonical.source
                    graph = canonical.graph
                    source_manifest = self.evidence.require(
                        store, task.task_name, PipelineStage.SOURCE
                    )
                    inventory_manifest = self.evidence.require(
                        store, task.task_name, PipelineStage.INVENTORY
                    )
                else:
                    if source_manifest is None:
                        source_started_at = self.evidence.clock()
                        resolved, downloaded, source = resolver(request, task)
                        source_completed_at = self.evidence.clock()
                        source_manifest = self.evidence.record_success(
                            store,
                            task.task_name,
                            PipelineStage.SOURCE,
                            json_digest(asdict(source)),
                            None,
                            started_at=source_started_at,
                            completed_at=source_completed_at,
                        )
                        source_input = inputs.save_source(
                            task=task,
                            resolved=resolved,
                            downloaded=downloaded,
                            source=source,
                            source_stage=source_manifest,
                        )
                    else:
                        source_manifest = self.evidence.require(
                            store, task.task_name, PipelineStage.SOURCE
                        )
                        source_input, resolved, downloaded = inputs.load_source(task)
                        source = source_input.source
                    inventory_started_at = self.evidence.clock()
                    graph = inspect_source_graph(downloaded.path, task=task)
                    inventory_completed_at = self.evidence.clock()
                    inventory_manifest = self.evidence.record_success(
                        store,
                        task.task_name,
                        PipelineStage.INVENTORY,
                        graph.graph_sha256,
                        source_manifest,
                        started_at=inventory_started_at,
                        completed_at=inventory_completed_at,
                    )
                    inputs.save_inventory(
                        task=task,
                        source_manifest=source_input,
                        graph=graph,
                        inventory_stage=inventory_manifest,
                    )
                context = SourceContext(
                    task,
                    resolved,
                    downloaded,
                    source,
                    graph,
                    source_manifest.started_at,
                    source_manifest.completed_at,
                    inventory_manifest.started_at,
                    inventory_manifest.completed_at,
                )
                policy_started_at = self.evidence.clock()
                preflight = validate_signing_preflight(
                    task,
                    context.graph,
                    repository_root=repository_root,
                    team_id="PREFLIGHTTEAM",
                    app_identifier_prefix="PREFLIGHTPREFIX.",
                )
                if not preflight.valid:
                    error = DomainError(
                        ErrorCode.SIGNING_PLAN_INVALID,
                        "current source inventory does not satisfy its signing policy",
                        task_name=task.task_name,
                        safe_details=(
                            (
                                "diagnostic_codes",
                                tuple(value.code for value in preflight.diagnostics),
                            ),
                        ),
                    )
                    self.evidence.record_failure(
                        store,
                        task.task_name,
                        PipelineStage.POLICY,
                        error,
                        inventory_manifest,
                        started_at=policy_started_at,
                    )
                    diagnostics.extend(
                        f"{task.task_name}:{value.code}" for value in preflight.diagnostics
                    )
                    continue
                self.evidence.record_success(
                    store,
                    task.task_name,
                    PipelineStage.POLICY,
                    json_digest(
                        {"policy": policy_sha256(task), "graph": context.graph.graph_sha256}
                    ),
                    inventory_manifest,
                    started_at=policy_started_at,
                )
                contexts.append(context)
            except SideloadedIPAError as error:
                if source_manifest is None:
                    stage = PipelineStage.SOURCE
                    predecessor = None
                elif inventory_manifest is None:
                    stage = PipelineStage.INVENTORY
                    predecessor = source_manifest
                else:
                    stage = PipelineStage.POLICY
                    predecessor = inventory_manifest
                self.evidence.record_failure(
                    store,
                    task.task_name,
                    stage,
                    error,
                    predecessor,
                    started_at=task_started_at,
                )
                diagnostics.append(f"{task.task_name}:{error.code.value}")
        if diagnostics:
            raise DomainError(
                ErrorCode.SIGNING_PLAN_INVALID,
                "production preflight found blocking diagnostics",
                remediation="resolve every reported task diagnostic before Apple apply",
                safe_details=(("diagnostics", tuple(diagnostics)),),
            )
        return tuple(contexts)
