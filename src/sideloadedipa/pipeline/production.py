"""Thin command-compatible coordinator for concrete production stages."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sideloadedipa.apple.commands import AppleCommandDependencies
from sideloadedipa.application import CommandName, CommandRequest, CommandResult
from sideloadedipa.cache.decisions import RebuildDecision
from sideloadedipa.cache.store import SigningCacheStore
from sideloadedipa.config import load_configuration
from sideloadedipa.domain.config import Task, TaskConfiguration
from sideloadedipa.domain.pipeline import (
    PipelineStage,
    SourceAsset,
    StageManifest,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode, SideloadedIPAError
from sideloadedipa.pipeline.cancellation import (
    SideEffectJournal,
    load_side_effect_journal,
    record_cancellation,
    route_sigterm_to_cancellation,
    write_side_effect_journal,
)
from sideloadedipa.pipeline.environment import (
    PipelineEnvironmentDependencies,
    selected_tasks,
)
from sideloadedipa.pipeline.input_manifests import CanonicalInputManifestStore
from sideloadedipa.pipeline.inspection import ResolvedSource
from sideloadedipa.pipeline.manifest_store import FileStageManifestStore
from sideloadedipa.pipeline.stages.apple import AppleStage
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import PreparedContext, SourceContext
from sideloadedipa.pipeline.stages.publication import PublicationStage
from sideloadedipa.pipeline.stages.results import command_result
from sideloadedipa.pipeline.stages.signing import SigningStage
from sideloadedipa.pipeline.stages.source_inventory import SourceInventoryStage
from sideloadedipa.pipeline.stages.verification import VerificationStage
from sideloadedipa.sources.download import DownloadedSource
from sideloadedipa.util.atomics import utc_now


@dataclass(frozen=True, slots=True)
class ProductionPipelineDependencies:
    package: PipelineEnvironmentDependencies = PipelineEnvironmentDependencies()
    apple: AppleCommandDependencies = AppleCommandDependencies()
    manifest_root: Path = Path("work/pipeline")
    report_root: Path = Path("work/reports")
    clock: Callable[[], datetime] = utc_now


class ProductionPipeline:
    def __init__(
        self,
        dependencies: ProductionPipelineDependencies = ProductionPipelineDependencies(),
        journal: SideEffectJournal | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.journal = journal
        self._evidence = StageEvidence(dependencies.manifest_root, dependencies.clock)
        self._source_inventory = SourceInventoryStage(dependencies.package, self._evidence)
        self._apple = AppleStage(dependencies.apple, self._evidence)
        self._signing = SigningStage(dependencies.package, self._evidence)
        self._verification = VerificationStage(
            self._signing,
            self._evidence,
            dependencies.report_root,
        )
        self._publication = PublicationStage(
            self._signing,
            self._verification,
            self._evidence,
        )

    def _default_production_request(
        self,
        request: CommandRequest,
        configuration: TaskConfiguration | None = None,
    ) -> CommandRequest:
        if request.task_names:
            return request
        current = configuration or load_configuration(request.config_path)
        task_names = tuple(task.task_name for task in current.tasks if task.publication_enabled)
        if not task_names:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "production selection has no publication-enabled tasks",
                remediation="explicitly enable at least one production task or select a canary task",
            )
        return replace(request, task_names=task_names)

    def _selected_tasks(
        self,
        request: CommandRequest,
        configuration: TaskConfiguration | None = None,
    ) -> tuple[Task, ...]:
        return selected_tasks(
            configuration or load_configuration(request.config_path),
            request.task_names,
            scope="production pipeline",
        )

    # Compatibility helpers remain intentionally thin for existing callers and tests.
    def _store(self, request: CommandRequest) -> FileStageManifestStore:
        return self._evidence.store(request.run_id)

    def _cache(self) -> SigningCacheStore:
        return self._signing.cache()

    def _require_signing_environment(self) -> None:
        self._signing.require_environment()

    def _source_path(self, request: CommandRequest, task: Task) -> Path:
        return self._source_inventory.source_path(request, task)

    def _source_selection_path(self, request: CommandRequest, task: Task) -> Path:
        return self._source_inventory.selection_path(request, task)

    def _inputs(self, request: CommandRequest) -> CanonicalInputManifestStore:
        return self._source_inventory.inputs(request)

    def _signing_report_path(self, request: CommandRequest, task_name: str) -> Path:
        return self._signing.signing_report_path(request, task_name)

    def _record_success(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
        result_sha256: str,
        predecessor: StageManifest | None,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> StageManifest:
        return self._evidence.record_success(
            store,
            task_name,
            stage,
            result_sha256,
            predecessor,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _record_failure(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
        error: SideloadedIPAError,
        predecessor: StageManifest | None,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        self._evidence.record_failure(
            store,
            task_name,
            stage,
            error,
            predecessor,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _resolve_source_asset(
        self,
        request: CommandRequest,
        task: Task,
    ) -> tuple[ResolvedSource, DownloadedSource, SourceAsset]:
        return self._source_inventory.resolve_source_asset(request, task)

    def _resolve_source(self, request: CommandRequest, task: Task) -> SourceContext:
        return self._source_inventory.resolve(request, task)

    def _load_contexts(
        self,
        request: CommandRequest,
        configuration: TaskConfiguration | None = None,
    ) -> tuple[SourceContext, ...]:
        return self._source_inventory.load(
            request,
            self._selected_tasks(request, configuration),
        )

    def _inspect_contexts(
        self,
        request: CommandRequest,
        configuration: TaskConfiguration | None = None,
    ) -> tuple[SourceContext, ...]:
        return self._source_inventory.inspect(
            request,
            self._selected_tasks(request, configuration),
            self._resolve_source_asset,
        )

    @contextmanager
    def _prepared(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
    ) -> Iterator[tuple[PreparedContext, ...]]:
        with self._signing.prepared(request, contexts) as prepared:
            yield prepared

    def _write_decisions(
        self,
        request: CommandRequest,
        decisions: tuple[RebuildDecision, ...],
    ) -> None:
        self._signing.write_decisions(request, decisions)

    def _read_decisions(self, request: CommandRequest) -> tuple[RebuildDecision, ...]:
        return self._signing.read_decisions(request)

    def inspect(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        contexts = self._inspect_contexts(request, configuration)
        return command_result(
            "inspect",
            {
                "status": "passed",
                "tasks": [
                    {
                        "task_name": value.task.task_name,
                        "source_sha256": value.source.sha256,
                        "graph_sha256": value.graph.graph_sha256,
                    }
                    for value in contexts
                ],
            },
            f"Production preflight: {len(contexts)} passed",
        )

    def plan(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        return self._apple.plan(
            request,
            self._load_contexts(request, configuration),
            configuration,
        )

    def sync(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        return self._apple.sync(
            request,
            self._load_contexts(request, configuration),
            configuration,
            self.journal,
        )

    def sign(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        self._require_signing_environment()
        return self._signing.sign(
            request,
            self._load_contexts(request, configuration),
            self._prepared,
        )

    def verify(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        self._require_signing_environment()
        return self._verification.verify(
            request,
            self._load_contexts(request, configuration),
            self._prepared,
        )

    def publish(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        self._require_signing_environment()
        return self._publication.publish(
            request,
            self._load_contexts(request, configuration),
            configuration,
            self._prepared,
            self.journal,
        )

    def run(self, request: CommandRequest) -> CommandResult:
        configuration = load_configuration(request.config_path)
        request = self._default_production_request(request, configuration)
        tasks = self._selected_tasks(request, configuration)
        if request.publish:
            disabled = tuple(task.task_name for task in tasks if not task.publication_enabled)
            if disabled:
                raise ConfigurationError(
                    ErrorCode.CONFIG_INVALID,
                    "selected tasks are not approved for publication",
                    remediation="complete physical-device acceptance before enabling publication",
                    safe_details=(("task_names", disabled),),
                )
        contexts = self._source_inventory.inspect(
            replace(request, command=CommandName.INSPECT),
            tasks,
            self._resolve_source_asset,
        )
        if not request.apply:
            return self._apple.plan(
                replace(request, command=CommandName.PLAN),
                contexts,
                configuration,
            )
        self._apple.sync(
            replace(request, command=CommandName.SYNC, apply=True),
            contexts,
            configuration,
            self.journal,
        )
        self._require_signing_environment()
        with self._prepared(request, contexts) as prepared:

            @contextmanager
            def reuse_prepared(
                _request: CommandRequest,
                _contexts: tuple[SourceContext, ...],
            ) -> Iterator[tuple[PreparedContext, ...]]:
                yield prepared

            self._signing.sign(
                replace(request, command=CommandName.SIGN),
                contexts,
                reuse_prepared,
            )
            self._verification.verify(
                replace(
                    request,
                    command=CommandName.VERIFY,
                    publish=request.publish,
                ),
                contexts,
                reuse_prepared,
            )
            if request.publish:
                return self._publication.publish(
                    replace(request, command=CommandName.PUBLISH),
                    contexts,
                    configuration,
                    reuse_prepared,
                    self.journal,
                )
        report = self.dependencies.report_root / f"{request.run_id}.json"
        return command_result(
            "run",
            {"status": "passed", "report_path": str(report)},
            "Production run: passed",
        )


def _execute_default(request: CommandRequest, operation: str) -> CommandResult:
    journal_path = FileStageManifestStore(Path("work/pipeline"), request.run_id).run_root / (
        "side-effects.json"
    )
    journal = load_side_effect_journal(journal_path)
    pipeline = ProductionPipeline(journal=journal)
    report = pipeline.dependencies.report_root / f"{request.run_id}-cancellation.json"
    try:
        with route_sigterm_to_cancellation(), record_cancellation(journal, report):
            handler = getattr(pipeline, operation)
            return handler(request)  # type: ignore[no-any-return]
    finally:
        write_side_effect_journal(journal_path, journal)


def inspect_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "inspect")


def plan_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "plan")


def sync_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "sync")


def sign_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "sign")


def verify_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "verify")


def publish_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "publish")


def run_command(request: CommandRequest) -> CommandResult:
    return _execute_default(request, "run")
