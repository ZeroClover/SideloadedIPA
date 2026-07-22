"""Integration tests for the real manifest-driven production composition."""

from __future__ import annotations

import hashlib
import json
import signal
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import sideloadedipa.pipeline.production as production
import sideloadedipa.pipeline.publish_stage as publish_stage
import sideloadedipa.pipeline.sign_stage as sign_stage
from sideloadedipa.application import CommandName, CommandRequest, CommandResult, OutputFormat
from sideloadedipa.cache.decisions import RebuildReason
from sideloadedipa.cache.fingerprint import SigningCacheFingerprint
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    Diagnostic,
    DiagnosticSeverity,
    PipelineStage,
    PublicationResult,
    SourceAsset,
    StageStatus,
    TaskConfiguration,
)
from sideloadedipa.errors import ConfigurationError, DomainError
from sideloadedipa.ipa.metadata import IpaMetadata
from sideloadedipa.pipeline.cancellation import SideEffectJournal
from sideloadedipa.pipeline.environment import PipelineEnvironmentDependencies
from sideloadedipa.pipeline.inspection import InspectDependencies, ResolvedSource
from sideloadedipa.pipeline.production import (
    PreparedContext,
    ProductionPipeline,
    ProductionPipelineDependencies,
    SourceContext,
)
from sideloadedipa.signing.preflight import PreflightResult
from sideloadedipa.sources import DownloadedSource
from tests.conftest import FixtureCopyBackend as CopyBackend
from tests.conftest import package_request as request_for

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


class IncrementingClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=10)
        return value


def command(
    tmp_path: Path,
    name: CommandName,
    *task_names: str,
    run_id: str = "run-one",
    publish: bool = False,
) -> CommandRequest:
    return CommandRequest(
        name,
        tmp_path / "configs/tasks.toml",
        task_names,
        OutputFormat.JSON,
        apply=name is CommandName.SYNC,
        publish=publish,
        run_id=run_id,
    )


def dependencies(tmp_path: Path) -> ProductionPipelineDependencies:
    environment = {
        "ZSIGN_BIN": str(tmp_path / "zsign"),
        "ZSIGN_SHA256": "a" * 64,
        "APPLE_DEV_CERT_P12_ENCODED": "ZmFrZQ==",
        "APPLE_DEV_CERT_PASSWORD": "secret",
    }
    return ProductionPipelineDependencies(
        package=PipelineEnvironmentDependencies(
            output_root=tmp_path / "signed",
            cache_root=tmp_path / "cache",
            profile_root=tmp_path / "profiles",
            environment=environment,
        ),
        manifest_root=tmp_path / "pipeline",
        report_root=tmp_path / "reports",
    )


def source_context(tmp_path: Path, task, graph: BundleGraph | None = None) -> SourceContext:  # type: ignore[no-untyped-def]
    path = tmp_path / f"{task.slug}.ipa"
    path.write_bytes(task.task_name.encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bundle_graph = graph or BundleGraph(PurePosixPath("Payload/App.app"), (), digest, "b" * 64)
    resolved = ResolvedSource(
        f"https://example.invalid/{task.slug}.ipa",
        digest,
        {
            "asset_id": task.task_name,
            "asset_name": path.name,
            "release_tag": "v1",
            "published_at": "2026-07-22T00:00:00Z",
        },
        path.stat().st_size,
    )
    downloaded = DownloadedSource(path, path.stat().st_size, digest)
    asset = SourceAsset(
        task.task_name,
        path.name,
        resolved.url,
        "v1",
        NOW,
        PurePosixPath(path.name),
        digest,
    )
    return SourceContext(task, resolved, downloaded, asset, bundle_graph)


def test_optional_icon_absence_or_processing_failure_is_non_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = load_configuration(Path("configs/tasks.toml")).tasks[0]
    context = source_context(tmp_path, configured)

    class Store:
        def upload_icon(self, slug: str, content: bytes) -> str:
            raise AssertionError("failed icon processing must not upload")

    assert (
        publish_stage._upload_icon(
            task=replace(configured, icon_path=None),
            source=context.source,
            source_evidence=context.resolved.evidence,
            artifact=context.downloaded.path,
            store=Store(),  # type: ignore[arg-type]
        )
        is None
    )

    monkeypatch.setattr(
        publish_stage,
        "build_icon_png",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("invalid icon")),
    )
    assert (
        publish_stage._upload_icon(
            task=replace(configured, icon_path="icons/example.png"),
            source=context.source,
            source_evidence=context.resolved.evidence,
            artifact=context.downloaded.path,
            store=Store(),  # type: ignore[arg-type]
        )
        is None
    )


def test_preflight_aggregates_all_tasks_before_apple_calls(tmp_path: Path, monkeypatch) -> None:
    tasks = load_configuration(Path("configs/tasks.toml")).tasks[:2]
    pipeline = ProductionPipeline(dependencies(tmp_path))
    contexts = {task.task_name: source_context(tmp_path, task) for task in tasks}
    apple_called = False

    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda path: TaskConfiguration(tasks),
    )
    monkeypatch.setattr(
        pipeline,
        "_resolve_source_asset",
        lambda request, task: (
            contexts[task.task_name].resolved,
            contexts[task.task_name].downloaded,
            contexts[task.task_name].source,
        ),
    )
    monkeypatch.setattr(
        production,
        "inspect_source_graph",
        lambda path, *, task: contexts[task.task_name].graph,
    )
    monkeypatch.setattr(
        production,
        "validate_signing_preflight",
        lambda task, graph, **kwargs: PreflightResult(
            (
                Diagnostic(
                    f"policy.{task.slug}",
                    DiagnosticSeverity.ERROR,
                    "fixture policy failure",
                    task_name=task.task_name,
                ),
            )
        ),
    )

    def apple_plan(request, deps):  # type: ignore[no-untyped-def]
        nonlocal apple_called
        apple_called = True
        return CommandResult()

    monkeypatch.setattr(production, "apple_plan_command", apple_plan)

    with pytest.raises(DomainError) as caught:
        pipeline.plan(command(tmp_path, CommandName.PLAN, *(task.task_name for task in tasks)))

    assert apple_called is False
    assert len(dict(caught.value.safe_details)["diagnostics"]) == 2
    for task in tasks:
        manifest = pipeline._store(command(tmp_path, CommandName.PLAN)).load(
            task.task_name, PipelineStage.POLICY
        )
        assert manifest is not None and manifest.status is StageStatus.FAILED


def test_source_failure_is_retained_as_preflight_evidence(tmp_path: Path, monkeypatch) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    pipeline = ProductionPipeline(dependencies(tmp_path))
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda path: TaskConfiguration((task,)),
    )

    def fail_source(request, selected):  # type: ignore[no-untyped-def]
        raise DomainError(
            production.ErrorCode.SOURCE_DOWNLOAD_FAILED,
            "fixture source failure",
            task_name=selected.task_name,
        )

    monkeypatch.setattr(pipeline, "_resolve_source_asset", fail_source)
    request = command(tmp_path, CommandName.INSPECT, task.task_name)

    with pytest.raises(DomainError):
        pipeline.inspect(request)

    manifest = pipeline._store(request).load(task.task_name, PipelineStage.SOURCE)
    assert manifest is not None
    assert manifest.status is StageStatus.FAILED
    assert manifest.diagnostics[0].code == "source.download_failed"
    pipeline._record_failure(
        pipeline._store(request),
        task.task_name,
        PipelineStage.SOURCE,
        DomainError(production.ErrorCode.CONFIG_INVALID, "replacement"),
        None,
    )
    assert pipeline._store(request).load(task.task_name, PipelineStage.SOURCE) == manifest
    with pytest.raises(DomainError):
        pipeline._record_success(
            pipeline._store(request),
            task.task_name,
            PipelineStage.SOURCE,
            "f" * 64,
            None,
        )


def test_visible_commands_extend_one_valid_manifest_chain(tmp_path: Path, monkeypatch) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    pipeline = ProductionPipeline(replace(dependencies(tmp_path), clock=IncrementingClock()))
    context = source_context(tmp_path, task)
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda path: TaskConfiguration((task,)),
    )
    monkeypatch.setattr(
        pipeline,
        "_resolve_source_asset",
        lambda request, selected: (context.resolved, context.downloaded, context.source),
    )
    monkeypatch.setattr(
        production,
        "inspect_source_graph",
        lambda path, *, task: context.graph,
    )
    monkeypatch.setattr(
        production,
        "validate_signing_preflight",
        lambda *args, **kwargs: PreflightResult(()),
    )
    monkeypatch.setattr(
        production,
        "apple_plan_command",
        lambda request, deps: CommandResult(payload=(("status", "ready"),)),
    )
    monkeypatch.setattr(
        production,
        "apple_sync_command",
        lambda request, deps: CommandResult(payload=(("status", "applied"),)),
    )
    request = command(tmp_path, CommandName.INSPECT, task.task_name)

    pipeline.inspect(request)
    pipeline.plan(replace(request, command=CommandName.PLAN))
    pipeline.sync(replace(request, command=CommandName.SYNC, apply=True))

    stages = pipeline._store(request).completed(task.task_name)
    assert [stage.stage for stage in stages] == list(PipelineStage)[:5]
    assert all(
        current.predecessor_sha256 == previous.manifest_sha256
        for previous, current in zip(stages, stages[1:])
    )
    assert all(
        stage.completed_at is not None and stage.completed_at > stage.started_at for stage in stages
    )


def _prime_apply_stages(
    pipeline: ProductionPipeline,
    request: CommandRequest,
    context: SourceContext,
) -> None:
    store = pipeline._store(request)
    predecessor = None
    for stage, digest in zip(
        list(PipelineStage)[:5],
        (
            context.source.sha256,
            context.graph.graph_sha256,
            "c" * 64,
            "d" * 64,
            "e" * 64,
        ),
    ):
        predecessor = pipeline._record_success(
            store,
            context.task.task_name,
            stage,
            digest,
            predecessor,
        )


def test_second_run_cache_hit_reopens_artifact_without_resigning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    signing_request = request_for(task, tmp_path)
    context = SourceContext(
        task,
        ResolvedSource("https://example.invalid/source.ipa", None, {}, None),
        DownloadedSource(
            signing_request.source_ipa,
            signing_request.source_ipa.stat().st_size,
            signing_request.graph.source_sha256,
        ),
        SourceAsset(
            "asset",
            signing_request.source_ipa.name,
            "https://example.invalid/source.ipa",
            "v1",
            NOW,
            PurePosixPath(signing_request.source_ipa.name),
            signing_request.graph.source_sha256,
        ),
        signing_request.graph,
    )
    fingerprint = SigningCacheFingerprint(1, task.task_name, (("task", task.task_name),), "a" * 64)
    prepared = PreparedContext(context, signing_request, fingerprint)
    pipeline = ProductionPipeline(dependencies(tmp_path))

    monkeypatch.setattr(pipeline, "_inspect_contexts", lambda request: (context,))

    @contextmanager
    def prepared_contexts(request, contexts):  # type: ignore[no-untyped-def]
        yield (prepared,)

    monkeypatch.setattr(pipeline, "_prepared", prepared_contexts)
    first = command(tmp_path, CommandName.SIGN, task.task_name, run_id="first")
    _prime_apply_stages(pipeline, first, context)

    first_result = pipeline.sign(first)
    pipeline.verify(replace(first, command=CommandName.VERIFY))

    assert dict(first_result.payload)["status"] == "passed"
    first_signing_report = pipeline._signing_report_path(first, task.task_name).read_bytes()
    signing_document = json.loads(first_signing_report)
    assert signing_document["nodes"]
    assert all(value["backend_evidence"] is not None for value in signing_document["nodes"])
    backend = signing_request.backend
    assert isinstance(backend, CopyBackend) and backend.called
    backend.called = False
    signing_request.destination_ipa.unlink()

    second = replace(first, run_id="second")
    _prime_apply_stages(pipeline, second, context)
    second_result = pipeline.sign(second)
    pipeline.verify(replace(second, command=CommandName.VERIFY))

    decisions = pipeline._read_decisions(second)
    assert decisions[0].reason is RebuildReason.CACHE_HIT
    assert dict(second_result.payload)["status"] == "passed"
    assert backend.called is False
    assert signing_request.destination_ipa.exists()
    assert (
        pipeline._signing_report_path(second, task.task_name).read_bytes() == first_signing_report
    )
    assert (tmp_path / "reports/second.json").is_file()

    cached_report = pipeline._cache().signing_report_path(task.task_name, fingerprint.sha256)
    cached_report.chmod(0o644)
    cached_report.write_bytes(b"tampered report")
    signing_request.destination_ipa.unlink()
    third = replace(first, run_id="third")
    _prime_apply_stages(pipeline, third, context)

    pipeline.sign(third)

    assert pipeline._read_decisions(third)[0].reason is RebuildReason.CACHE_REJECTED
    assert backend.called is True

    backend.called = False
    cached_artifact = pipeline._cache().artifact_path(task.task_name, fingerprint.sha256)
    cached_artifact.chmod(0o644)
    cached_artifact.write_bytes(b"tampered cache")
    signing_request.destination_ipa.unlink()
    fourth = replace(first, run_id="fourth")
    _prime_apply_stages(pipeline, fourth, context)

    pipeline.sign(fourth)

    assert pipeline._read_decisions(fourth)[0].reason is RebuildReason.CACHE_REJECTED
    assert backend.called is True


def test_default_stage_wrapper_records_created_resources_on_cancellation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class InterruptedPipeline:
        def __init__(self, *, journal):  # type: ignore[no-untyped-def]
            self.journal = journal
            self.dependencies = SimpleNamespace(report_root=tmp_path / "reports")

        def sync(self, request):  # type: ignore[no-untyped-def]
            self.journal.record_apple_resource("profile", "PROFILE_NEW")
            return CommandResult()

        def sign(self, request):  # type: ignore[no-untyped-def]
            signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(production, "ProductionPipeline", InterruptedPipeline)
    request = command(tmp_path, CommandName.SYNC, run_id="cancelled")

    monkeypatch.chdir(tmp_path)
    production._execute_default(request, "sync")
    with pytest.raises(KeyboardInterrupt):
        production._execute_default(replace(request, command=CommandName.SIGN), "sign")

    document = json.loads((tmp_path / "reports/cancelled-cancellation.json").read_text())
    assert document["created_apple_resources"] == [
        {"kind": "profile", "resource_id": "PROFILE_NEW"}
    ]
    assert document["publication_committed"] is False


def test_run_rejects_publication_disabled_task_before_signing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))
    task = replace(configuration.tasks[0], publication_enabled=False)
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda _path: replace(configuration, tasks=(task,)),
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    monkeypatch.setattr(
        pipeline,
        "inspect",
        lambda _request: pytest.fail("disabled task reached production stages"),
    )

    with pytest.raises(ConfigurationError, match="not approved"):
        pipeline.run(
            command(
                tmp_path,
                CommandName.RUN,
                task.task_name,
                publish=True,
            )
        )


def test_default_production_selection_ignores_publication_disabled_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))
    enabled = configuration.tasks[0]
    disabled = replace(configuration.tasks[1], publication_enabled=False)
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda _path: replace(configuration, tasks=(enabled, disabled)),
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    selected: list[tuple[str, ...]] = []

    def inspect_contexts(request):  # type: ignore[no-untyped-def]
        selected.append(request.task_names)
        return ()

    monkeypatch.setattr(pipeline, "_inspect_contexts", inspect_contexts)

    result = pipeline.inspect(command(tmp_path, CommandName.INSPECT))

    assert dict(result.payload)["status"] == "passed"
    assert selected == [(enabled.task_name,)]


def test_default_production_selection_rejects_an_empty_publication_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))
    disabled = tuple(replace(task, publication_enabled=False) for task in configuration.tasks)
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda _path: replace(configuration, tasks=disabled),
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    monkeypatch.setattr(
        pipeline,
        "_inspect_contexts",
        lambda _request: pytest.fail("empty production selection reached inspection"),
    )

    with pytest.raises(ConfigurationError, match="no publication-enabled tasks"):
        pipeline.inspect(command(tmp_path, CommandName.INSPECT))


def test_publish_reverifies_records_and_promotes_cache(tmp_path: Path, monkeypatch) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    signing_request = request_for(task, tmp_path)
    context = SourceContext(
        task,
        ResolvedSource(
            "https://example.invalid/source.ipa",
            None,
            {"release_tag": "v1"},
            None,
        ),
        DownloadedSource(
            signing_request.source_ipa,
            signing_request.source_ipa.stat().st_size,
            signing_request.graph.source_sha256,
        ),
        SourceAsset(
            "asset",
            signing_request.source_ipa.name,
            "https://example.invalid/source.ipa",
            "v1",
            NOW,
            PurePosixPath(signing_request.source_ipa.name),
            signing_request.graph.source_sha256,
        ),
        signing_request.graph,
    )
    fingerprint = SigningCacheFingerprint(
        1,
        task.task_name,
        (("task", task.task_name),),
        "f" * 64,
    )
    prepared = PreparedContext(context, signing_request, fingerprint)
    journal = SideEffectJournal()
    pipeline = ProductionPipeline(dependencies(tmp_path), journal)
    request = command(
        tmp_path,
        CommandName.SIGN,
        task.task_name,
        run_id="published",
        publish=True,
    )
    _prime_apply_stages(pipeline, request, context)

    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda path: TaskConfiguration((task,)),
    )
    monkeypatch.setattr(pipeline, "_inspect_contexts", lambda selected: (context,))

    @contextmanager
    def prepared_contexts(selected, contexts):  # type: ignore[no-untyped-def]
        yield (prepared,)

    monkeypatch.setattr(pipeline, "_prepared", prepared_contexts)
    pipeline.sign(request)
    pipeline.verify(replace(request, command=CommandName.VERIFY))

    class PublicationStore:
        def upload_icon(self, slug, content):  # type: ignore[no-untyped-def]
            assert slug == task.slug
            assert content == b"icon"
            return "https://downloads.example/icon.png"

    class Publisher:
        def publish(self, candidates, *, now):  # type: ignore[no-untyped-def]
            candidate = candidates[0]
            assert candidate.icon_url == "https://downloads.example/icon.png"
            assert candidate.bundle_id == "com.example.app"
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
        production,
        "publication_runtime",
        lambda configuration, environment: (PublicationStore(), Publisher()),
    )
    monkeypatch.setattr(
        publish_stage, "read_ipa_metadata", lambda path: IpaMetadata("com.example.app", "1.0")
    )
    monkeypatch.setattr(publish_stage, "build_icon_png", lambda *args, **kwargs: b"icon")

    result = pipeline.publish(replace(request, command=CommandName.PUBLISH))

    payload = dict(result.payload)
    assert payload["status"] == "passed"
    assert journal.publication_committed is True
    assert pipeline._cache().load() is not None
    assert pipeline._store(request).load(task.task_name, PipelineStage.PUBLISH) is not None
    report = json.loads((tmp_path / "reports/published.json").read_text())
    assert report["tasks"][0]["publication"]["artifact_key"].endswith("App.ipa")


def test_run_composes_visible_stages_and_supports_plan_only(tmp_path: Path, monkeypatch) -> None:
    pipeline = ProductionPipeline(dependencies(tmp_path))
    calls: list[tuple[str, bool, bool]] = []
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda _path: load_configuration(Path("configs/tasks.toml")),
    )

    def handler(name: str):
        def execute(request):  # type: ignore[no-untyped-def]
            calls.append((name, request.apply, request.publish))
            return CommandResult(payload=(("stage", name),))

        return execute

    for name in ("inspect", "plan", "sync", "sign", "verify", "publish"):
        monkeypatch.setattr(pipeline, name, handler(name))

    plan_only = command(tmp_path, CommandName.RUN, run_id="plan-only")
    assert dict(pipeline.run(plan_only).payload)["stage"] == "plan"
    assert [name for name, _, _ in calls] == ["inspect", "plan"]

    calls.clear()
    applied = replace(plan_only, apply=True)
    assert dict(pipeline.run(applied).payload)["status"] == "passed"
    assert [name for name, _, _ in calls] == ["inspect", "plan", "sync", "sign", "verify"]
    assert calls[-1] == ("verify", True, False)

    calls.clear()
    published = replace(applied, publish=True)
    assert dict(pipeline.run(published).payload)["stage"] == "publish"
    assert [name for name, _, _ in calls][-2:] == ["verify", "publish"]
    assert calls[-2] == ("verify", True, True)


def test_run_defaults_every_stage_to_publication_enabled_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))
    enabled = configuration.tasks[0]
    disabled = replace(configuration.tasks[1], publication_enabled=False)
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda _path: replace(configuration, tasks=(enabled, disabled)),
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    calls: list[tuple[str, tuple[str, ...]]] = []

    def handler(name: str):
        def execute(request):  # type: ignore[no-untyped-def]
            calls.append((name, request.task_names))
            return CommandResult(payload=(("stage", name),))

        return execute

    for name in ("inspect", "plan", "sync", "sign", "verify", "publish"):
        monkeypatch.setattr(pipeline, name, handler(name))

    request = replace(
        command(tmp_path, CommandName.RUN),
        apply=True,
        publish=True,
    )
    assert dict(pipeline.run(request).payload)["stage"] == "publish"
    assert [name for name, _ in calls] == ["inspect", "plan", "sync", "sign", "verify", "publish"]
    assert {task_names for _, task_names in calls} == {(enabled.task_name,)}


def test_run_rejects_unknown_task_before_stages(tmp_path: Path, monkeypatch) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    monkeypatch.setattr(
        production,
        "load_configuration",
        lambda path: TaskConfiguration((task,)),
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    monkeypatch.setattr(
        pipeline,
        "inspect",
        lambda _request: pytest.fail("unknown task reached production stages"),
    )

    with pytest.raises(ConfigurationError, match="selection is invalid"):
        pipeline.run(command(tmp_path, CommandName.RUN, "missing", publish=True))


def test_source_resolution_persists_current_asset_for_the_run(tmp_path: Path, monkeypatch) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    graph = BundleGraph(PurePosixPath("Payload/App.app"), (), "a" * 64, "b" * 64)
    resolved = ResolvedSource(
        "https://example.invalid/app.ipa",
        f"sha256:{hashlib.sha256(b'source').hexdigest()}",
        {"asset_name": "app.ipa", "release_tag": "v1"},
        6,
    )
    downloads = 0
    resolutions = 0

    def download(url, destination, *, expected_sha256):  # type: ignore[no-untyped-def]
        nonlocal downloads
        downloads += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"source")
        return DownloadedSource(destination, 6, hashlib.sha256(b"source").hexdigest())

    package = replace(
        dependencies(tmp_path).package,
        inspect=InspectDependencies(download=download),
    )
    pipeline = ProductionPipeline(replace(dependencies(tmp_path), package=package))

    def select_source(*args):  # type: ignore[no-untyped-def]
        nonlocal resolutions
        resolutions += 1
        return resolved

    monkeypatch.setattr(production, "resolve_source", select_source)
    monkeypatch.setattr(production, "inspect_source_graph", lambda path, *, task: graph)
    request = command(tmp_path, CommandName.INSPECT, task.task_name)

    first = pipeline._resolve_source(request, task)
    second = pipeline._resolve_source(request, task)

    assert first.source.sha256 == resolved.expected_sha256.removeprefix("sha256:")
    assert second.downloaded.path == first.downloaded.path
    assert downloads == 1
    assert resolutions == 1
    assert pipeline._source_selection_path(request, task).is_file()


def test_prepared_context_builds_private_signing_inputs_and_complete_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    signing_request = request_for(task, tmp_path)
    context = SourceContext(
        task,
        ResolvedSource("https://example.invalid/source.ipa", None, {}, None),
        DownloadedSource(
            signing_request.source_ipa,
            signing_request.source_ipa.stat().st_size,
            signing_request.graph.source_sha256,
        ),
        SourceAsset(
            "asset",
            signing_request.source_ipa.name,
            "https://example.invalid/source.ipa",
            "v1",
            NOW,
            PurePosixPath(signing_request.source_ipa.name),
            signing_request.graph.source_sha256,
        ),
        signing_request.graph,
    )
    pipeline = ProductionPipeline(dependencies(tmp_path))
    monkeypatch.setattr(
        production,
        "prepare_package_signing",
        lambda **kwargs: signing_request,
    )

    with pipeline._prepared(
        command(tmp_path, CommandName.SIGN, task.task_name),
        (context,),
    ) as prepared:
        assert prepared[0].request is signing_request
        assert prepared[0].fingerprint.task_name == task.task_name

    assert sign_stage.device_set_sha256(signing_request)
    livecontainer = next(
        value
        for value in load_configuration(Path("configs/tasks.toml")).tasks
        if value.task_name == "LiveContainer"
    )
    template_digests = sign_stage.template_digests(livecontainer, Path.cwd())
    assert len(template_digests) == 2
    assert len({path for path, _ in template_digests}) == 1
