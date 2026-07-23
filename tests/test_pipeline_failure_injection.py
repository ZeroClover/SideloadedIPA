"""Failure injection at every real production pipeline stage boundary."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

import sideloadedipa.pipeline.production as production
import sideloadedipa.pipeline.publish_stage as publish_stage
import sideloadedipa.pipeline.stages.apple as production_apple_stage
import sideloadedipa.pipeline.stages.publication as production_publication_stage
import sideloadedipa.pipeline.stages.source_inventory as source_inventory_stage
import sideloadedipa.pipeline.stages.verification as production_verification_stage
from sideloadedipa.application import CommandName, CommandResult
from sideloadedipa.cache.fingerprint import SigningCacheFingerprint
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    PipelineStage,
    PublicationResult,
    TaskConfiguration,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa.graph import canonical_graph_json
from sideloadedipa.ipa.metadata import IpaMetadata
from sideloadedipa.pipeline.production import PreparedContext, ProductionPipeline
from sideloadedipa.signing.preflight import PreflightResult
from sideloadedipa.sources import DownloadedSource
from sideloadedipa.util.atomics import canonical_json
from tests.conftest import FixtureCopyBackend as CopyBackend
from tests.conftest import package_request as request_for
from tests.conftest import production_command as command
from tests.conftest import production_dependencies as dependencies
from tests.conftest import production_source_context as source_context


@pytest.mark.parametrize("failed_stage", tuple(PipelineStage))
def test_failure_blocks_every_downstream_production_stage_and_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: PipelineStage,
) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    signing_request = request_for(task, tmp_path)
    graph_document = json.loads(canonical_graph_json(signing_request.graph))
    graph_document.pop("graph_sha256")
    graph = replace(
        signing_request.graph,
        graph_sha256=hashlib.sha256(canonical_json(graph_document)).hexdigest(),
    )
    context = source_context(tmp_path, task, graph)
    source_path = signing_request.source_ipa
    source_sha256 = graph.source_sha256
    context = replace(
        context,
        resolved=replace(
            context.resolved,
            expected_sha256=f"sha256:{source_sha256}",
            evidence={**context.resolved.evidence, "kind": task.source.kind.value},
            advertised_size=source_path.stat().st_size,
        ),
        downloaded=DownloadedSource(
            source_path,
            source_path.stat().st_size,
            source_sha256,
        ),
        source=replace(
            context.source,
            path=PurePosixPath(source_path.name),
            sha256=source_sha256,
        ),
    )
    prepared = PreparedContext(
        context,
        signing_request,
        SigningCacheFingerprint(2, task.task_name, (("task", task.task_name),), "a" * 64),
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    request = command(
        tmp_path,
        CommandName.RUN,
        task.task_name,
        run_id=f"fail-{failed_stage.value}",
        publish=True,
    )
    request = replace(request, apply=True)
    attempted: list[PipelineStage] = []
    apple_effects: list[PipelineStage] = []
    verify_calls = 0
    publish_calls = 0

    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda path: TaskConfiguration((task,)),
    )

    def resolve_source(selected, value):  # type: ignore[no-untyped-def]
        del value
        path = pipeline._source_path(selected, task)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(context.downloaded.path.read_bytes())
        return (
            context.resolved,
            replace(context.downloaded, path=path),
            replace(context.source, path=PurePosixPath(path.name)),
        )

    def inspect_graph(path, *, task):  # type: ignore[no-untyped-def]
        del path, task
        return context.graph

    monkeypatch.setattr(pipeline, "_resolve_source_asset", resolve_source)
    monkeypatch.setattr(source_inventory_stage, "inspect_source_graph", inspect_graph)
    monkeypatch.setattr(
        source_inventory_stage,
        "validate_signing_preflight",
        lambda *args, **kwargs: PreflightResult(()),
    )

    def apple_plan(selected, deps):  # type: ignore[no-untyped-def]
        del selected, deps
        apple_effects.append(PipelineStage.RESOURCE_PLAN)
        return CommandResult(payload=(("status", "ready"),))

    def apple_sync(selected, deps):  # type: ignore[no-untyped-def]
        del selected, deps
        apple_effects.append(PipelineStage.RESOURCE_APPLY)
        return CommandResult(payload=(("status", "applied"),))

    monkeypatch.setattr(production_apple_stage, "apple_plan_command", apple_plan)
    monkeypatch.setattr(production_apple_stage, "apple_sync_command", apple_sync)

    @contextmanager
    def prepared_contexts(selected, contexts):  # type: ignore[no-untyped-def]
        del selected, contexts
        yield (prepared,)

    monkeypatch.setattr(pipeline, "_prepared", prepared_contexts)

    original_verify = production_verification_stage.verify_package_artifact

    def verify_artifact(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal verify_calls
        verify_calls += 1
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(
        production_verification_stage,
        "verify_package_artifact",
        verify_artifact,
    )
    monkeypatch.setattr(
        production_publication_stage,
        "verify_package_artifact",
        verify_artifact,
    )

    class PublicationStore:
        def upload_icon(self, slug, content):  # type: ignore[no-untyped-def]
            del slug, content
            return "https://downloads.example/icon.png"

    class Publisher:
        def publish(self, candidates, *, now):  # type: ignore[no-untyped-def]
            nonlocal publish_calls
            del now
            publish_calls += 1
            candidate = candidates[0]
            return (
                PublicationResult(
                    task.task_name,
                    "apps/example/1.0/App.ipa",
                    "https://downloads.example/App.ipa",
                    candidate.artifact_sha256,
                    "site/apps.json",
                    "9" * 64,
                ),
            )

    monkeypatch.setattr(
        production_publication_stage,
        "publication_runtime",
        lambda configuration, environment: (PublicationStore(), Publisher()),
    )
    monkeypatch.setattr(
        publish_stage, "read_ipa_metadata", lambda path: IpaMetadata("com.example.app", "1.0")
    )
    monkeypatch.setattr(publish_stage, "build_icon_png", lambda *args, **kwargs: b"icon")

    evidence_type = type(pipeline._evidence)
    original_record = evidence_type.record_success

    def record_success(
        evidence,
        store,
        task_name,
        stage,
        result_sha256,
        predecessor,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        if store.load(task_name, stage) is None:
            attempted.append(stage)
            if stage is failed_stage:
                raise DomainError(
                    ErrorCode.DOMAIN_INVARIANT,
                    "injected production stage failure",
                    task_name=task_name,
                )
        return original_record(
            evidence,
            store,
            task_name,
            stage,
            result_sha256,
            predecessor,
            **kwargs,
        )

    monkeypatch.setattr(evidence_type, "record_success", record_success)

    with pytest.raises(DomainError):
        pipeline.run(request)

    failed_index = tuple(PipelineStage).index(failed_stage)
    assert attempted == list(PipelineStage)[: failed_index + 1]
    store = pipeline._store(request)
    assert all(
        store.load(task.task_name, stage) is None
        for stage in tuple(PipelineStage)[failed_index + 1 :]
    )
    assert apple_effects == [
        stage
        for stage in (PipelineStage.RESOURCE_PLAN, PipelineStage.RESOURCE_APPLY)
        if tuple(PipelineStage).index(stage) <= failed_index
    ]
    backend = signing_request.backend
    assert isinstance(backend, CopyBackend)
    assert backend.called is (failed_index >= tuple(PipelineStage).index(PipelineStage.SIGN))
    expected_verify_calls = 0
    if failed_index >= tuple(PipelineStage).index(PipelineStage.VERIFY):
        expected_verify_calls += 1
    if failed_stage is PipelineStage.PUBLISH:
        expected_verify_calls += 1
    assert verify_calls == expected_verify_calls
    assert publish_calls == (1 if failed_stage is PipelineStage.PUBLISH else 0)
