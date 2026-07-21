"""Failure injection at every pipeline stage boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.domain import PipelineStage, StageManifest, thaw_json
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.pipeline_application import (
    ManifestPipelineUseCases,
    StageOperation,
    StageOutput,
)


@dataclass
class IncrementingClock:
    current: datetime = datetime(2026, 7, 21, tzinfo=timezone.utc)

    def now(self) -> datetime:
        result = self.current
        self.current += timedelta(milliseconds=10)
        return result


@dataclass
class ManifestStore:
    values: dict[tuple[str, PipelineStage], StageManifest] = field(default_factory=dict)

    def load(self, task_name: str, stage: PipelineStage) -> StageManifest | None:
        return self.values.get((task_name, stage))

    def save(self, manifest: StageManifest) -> None:
        self.values[(manifest.task_name, manifest.stage)] = manifest


@dataclass
class FailureInjector:
    fail_at: PipelineStage
    calls: list[PipelineStage] = field(default_factory=list)
    side_effects: list[PipelineStage] = field(default_factory=list)

    def operation(self, stage: PipelineStage) -> StageOperation:
        def execute(
            request: CommandRequest,
            task_name: str,
            predecessor: StageManifest | None,
        ) -> StageOutput:
            del request, predecessor
            self.calls.append(stage)
            if stage is self.fail_at:
                raise DomainError(
                    ErrorCode.DOMAIN_INVARIANT,
                    "injected stage-boundary failure",
                    task_name=task_name,
                )
            if stage in {
                PipelineStage.RESOURCE_APPLY,
                PipelineStage.SIGN,
                PipelineStage.VERIFY,
                PipelineStage.PUBLISH,
            }:
                self.side_effects.append(stage)
            digest = hashlib.sha256(stage.value.encode()).hexdigest()
            return StageOutput(digest)

        return execute


def request() -> CommandRequest:
    return CommandRequest(
        CommandName.RUN,
        Path("configs/tasks.toml"),
        ("Example",),
        OutputFormat.JSON,
        apply=True,
        publish=True,
    )


@pytest.mark.parametrize("failed_stage", tuple(PipelineStage))
def test_failure_blocks_every_downstream_operation_and_side_effect(
    failed_stage: PipelineStage,
) -> None:
    injector = FailureInjector(failed_stage)
    store = ManifestStore()
    use_cases = ManifestPipelineUseCases(
        {stage: injector.operation(stage) for stage in PipelineStage},
        store,
        IncrementingClock(),
        lambda value: value.task_names,
    )

    result = use_cases.run(request())
    payload = {key: thaw_json(value) for key, value in result.payload}
    task = cast(list[dict[str, object]], payload["tasks"])[0]
    stage_documents = cast(list[dict[str, object]], task["stages"])
    manifests = [cast(dict[str, object], value["manifest"]) for value in stage_documents]
    failed_index = tuple(PipelineStage).index(failed_stage)

    assert result.exit_code == 1
    assert injector.calls == list(PipelineStage)[: failed_index + 1]
    assert [value["status"] for value in manifests[:failed_index]] == ["succeeded"] * failed_index
    assert manifests[failed_index]["status"] == "failed"
    assert [value["status"] for value in manifests[failed_index + 1 :]] == ["skipped"] * (
        len(PipelineStage) - failed_index - 1
    )

    effect_stages = {
        PipelineStage.RESOURCE_APPLY,
        PipelineStage.SIGN,
        PipelineStage.VERIFY,
        PipelineStage.PUBLISH,
    }
    assert injector.side_effects == [
        stage for stage in list(PipelineStage)[:failed_index] if stage in effect_stages
    ]
    assert not any(stage in injector.calls for stage in list(PipelineStage)[failed_index + 1 :])
