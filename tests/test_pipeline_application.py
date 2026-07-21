"""Tests for manifest-driven command use cases."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.domain import PipelineStage, StageManifest, thaw_json
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.pipeline_application import (
    ManifestPipelineUseCases,
    StageOperation,
    StageOutput,
)


@dataclass
class FixtureClock:
    current: datetime = datetime(2026, 7, 21, tzinfo=timezone.utc)

    def now(self) -> datetime:
        result = self.current
        self.current += timedelta(seconds=1)
        return result


@dataclass
class MemoryManifestStore:
    values: dict[tuple[str, PipelineStage], StageManifest] = field(default_factory=dict)

    def load(self, task_name: str, stage: PipelineStage) -> StageManifest | None:
        return self.values.get((task_name, stage))

    def save(self, manifest: StageManifest) -> None:
        self.values[(manifest.task_name, manifest.stage)] = manifest


@dataclass
class RecordingOperations:
    calls: list[tuple[str, PipelineStage, PipelineStage | None]] = field(default_factory=list)
    fail_stage: PipelineStage | None = None

    def operation(self, stage: PipelineStage) -> StageOperation:
        def execute(
            request: CommandRequest,
            task_name: str,
            predecessor: StageManifest | None,
        ) -> StageOutput:
            del request
            self.calls.append(
                (task_name, stage, predecessor.stage if predecessor is not None else None)
            )
            if stage is self.fail_stage:
                raise DomainError(
                    ErrorCode.DOMAIN_INVARIANT,
                    "fixture stage failed",
                    task_name=task_name,
                )
            digest = hashlib.sha256(f"{task_name}:{stage.value}".encode()).hexdigest()
            return StageOutput(digest, (("stage", stage.value),), f"{task_name}: {stage.value}")

        return execute


def request(command: CommandName, *, publish: bool = False) -> CommandRequest:
    return CommandRequest(
        command,
        Path("unused.toml"),
        ("Example",),
        OutputFormat.JSON,
        apply=True,
        publish=publish,
    )


def use_cases(
    recorder: RecordingOperations,
    store: MemoryManifestStore,
) -> ManifestPipelineUseCases:
    return ManifestPipelineUseCases(
        {stage: recorder.operation(stage) for stage in PipelineStage},
        store,
        FixtureClock(),
        lambda value: value.task_names,
    )


def test_each_command_consumes_the_persisted_predecessor_manifest() -> None:
    store = MemoryManifestStore()
    recorder = RecordingOperations()
    pipeline = use_cases(recorder, store)

    assert pipeline.inspect(request(CommandName.INSPECT)).exit_code == 0
    assert pipeline.plan(request(CommandName.PLAN)).exit_code == 0
    assert pipeline.sync(request(CommandName.SYNC)).exit_code == 0
    assert pipeline.sign(request(CommandName.SIGN)).exit_code == 0
    assert pipeline.verify(request(CommandName.VERIFY)).exit_code == 0

    assert [(stage, predecessor) for _, stage, predecessor in recorder.calls] == [
        (PipelineStage.SOURCE, None),
        (PipelineStage.INVENTORY, PipelineStage.SOURCE),
        (PipelineStage.POLICY, PipelineStage.INVENTORY),
        (PipelineStage.RESOURCE_PLAN, PipelineStage.POLICY),
        (PipelineStage.RESOURCE_APPLY, PipelineStage.RESOURCE_PLAN),
        (PipelineStage.SIGNING_PLAN, PipelineStage.RESOURCE_APPLY),
        (PipelineStage.SIGN, PipelineStage.SIGNING_PLAN),
        (PipelineStage.VERIFY, PipelineStage.SIGN),
    ]


def test_run_executes_full_chain_and_publishes_only_when_requested() -> None:
    without_publish = RecordingOperations()
    result = use_cases(without_publish, MemoryManifestStore()).run(request(CommandName.RUN))
    with_publish = RecordingOperations()
    published = use_cases(with_publish, MemoryManifestStore()).run(
        request(CommandName.RUN, publish=True)
    )

    assert result.exit_code == 0
    assert without_publish.calls[-1][1] is PipelineStage.VERIFY
    assert published.exit_code == 0
    assert with_publish.calls[-1][1] is PipelineStage.PUBLISH


def test_failure_records_diagnostic_and_skips_all_downstream_stages() -> None:
    store = MemoryManifestStore()
    recorder = RecordingOperations(fail_stage=PipelineStage.POLICY)
    pipeline = use_cases(recorder, store)
    pipeline.inspect(request(CommandName.INSPECT))

    result = pipeline.plan(request(CommandName.PLAN))
    payload = {key: thaw_json(value) for key, value in result.payload}
    tasks = cast(list[dict[str, object]], payload["tasks"])
    stages = cast(list[dict[str, object]], tasks[0]["stages"])

    assert result.exit_code == 1
    manifests = [cast(dict[str, object], value["manifest"]) for value in stages]
    assert [value["status"] for value in manifests] == ["failed", "skipped"]
    diagnostic = cast(dict[str, object], stages[0]["diagnostic"])
    assert diagnostic["code"] == "domain.invariant"
    assert recorder.calls[-1][1] is PipelineStage.POLICY


def test_command_fails_before_operation_when_predecessor_is_missing() -> None:
    recorder = RecordingOperations()
    pipeline = use_cases(recorder, MemoryManifestStore())

    try:
        pipeline.sign(request(CommandName.SIGN))
    except DomainError as error:
        assert error.code is ErrorCode.PIPELINE_TRANSITION_INVALID
        assert "resource-apply" in (error.remediation or "")
    else:
        raise AssertionError("missing predecessor was accepted")
    assert recorder.calls == []


def test_application_exposes_all_six_manifest_driven_use_cases() -> None:
    recorder = RecordingOperations()
    pipeline = use_cases(recorder, MemoryManifestStore())
    application = pipeline.application()

    result = application.execute(request(CommandName.RUN, publish=True))

    assert result.exit_code == 0
    assert len(recorder.calls) == len(PipelineStage)
