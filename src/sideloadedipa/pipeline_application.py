"""Manifest-driven use cases for every pipeline command."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from sideloadedipa.application import Application, CommandRequest, CommandResult
from sideloadedipa.domain import (
    FrozenJsonObject,
    FrozenJsonValue,
    PipelineStage,
    StageManifest,
    StageStatus,
    freeze_json,
    thaw_json,
)
from sideloadedipa.errors import DomainError, ErrorCode, SideloadedIPAError
from sideloadedipa.ports import Clock
from sideloadedipa.stage_manifests import (
    canonical_stage_manifest_json,
    finish_stage,
    skip_stage,
    start_stage,
)


@dataclass(frozen=True, slots=True)
class StageOutput:
    result_sha256: str
    payload: tuple[tuple[str, FrozenJsonValue], ...] = ()
    human_output: str | None = None


class StageOperation(Protocol):
    def __call__(
        self,
        request: CommandRequest,
        task_name: str,
        predecessor: StageManifest | None,
    ) -> StageOutput: ...


class StageManifestStore(Protocol):
    def load(self, task_name: str, stage: PipelineStage) -> StageManifest | None: ...

    def save(self, manifest: StageManifest) -> None: ...


TaskSelector = Callable[[CommandRequest], tuple[str, ...]]

_COMMAND_STAGES = {
    "inspect": (PipelineStage.SOURCE, PipelineStage.INVENTORY),
    "plan": (PipelineStage.POLICY, PipelineStage.RESOURCE_PLAN),
    "sync": (PipelineStage.RESOURCE_APPLY,),
    "sign": (PipelineStage.SIGNING_PLAN, PipelineStage.SIGN),
    "verify": (PipelineStage.VERIFY,),
}
_PREDECESSORS = {
    "plan": PipelineStage.INVENTORY,
    "sync": PipelineStage.RESOURCE_PLAN,
    "sign": PipelineStage.RESOURCE_APPLY,
    "verify": PipelineStage.SIGN,
}


def _manifest_document(manifest: StageManifest) -> dict[str, object]:
    document = json.loads(canonical_stage_manifest_json(manifest))
    if not isinstance(document, dict):
        raise TypeError("stage manifest root must be an object")
    return cast(dict[str, object], document)


def _diagnostic_document(error: SideloadedIPAError) -> dict[str, object]:
    diagnostic = error.to_diagnostic()
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "task_name": diagnostic.task_name,
        "bundle_id": diagnostic.bundle_id,
        "remediation": diagnostic.remediation,
        "details": {key: thaw_json(value) for key, value in diagnostic.details},
    }


@dataclass(frozen=True, slots=True)
class ManifestPipelineUseCases:
    operations: Mapping[PipelineStage, StageOperation]
    manifests: StageManifestStore
    clock: Clock
    select_tasks: TaskSelector

    def _predecessor(
        self,
        request: CommandRequest,
        task_name: str,
    ) -> StageManifest | None:
        stage = _PREDECESSORS.get(request.command.value)
        if stage is None:
            return None
        predecessor = self.manifests.load(task_name, stage)
        if predecessor is None:
            raise DomainError(
                ErrorCode.PIPELINE_TRANSITION_INVALID,
                "required predecessor manifest is missing",
                task_name=task_name,
                remediation=f"complete the {stage.value} stage first",
                safe_details=(("stage", stage.value),),
            )
        return predecessor

    def _execute_task(
        self,
        request: CommandRequest,
        task_name: str,
        stages: tuple[PipelineStage, ...],
        predecessor: StageManifest | None,
    ) -> tuple[list[dict[str, object]], list[str], bool]:
        documents: list[dict[str, object]] = []
        human: list[str] = []
        previous = predecessor
        for index, stage in enumerate(stages):
            operation = self.operations.get(stage)
            if operation is None:
                raise DomainError(
                    ErrorCode.PIPELINE_TRANSITION_INVALID,
                    "pipeline stage operation is not configured",
                    task_name=task_name,
                    remediation=f"configure the {stage.value} stage adapter",
                    safe_details=(("stage", stage.value),),
                )
            running = start_stage(
                task_name=task_name,
                stage=stage,
                started_at=self.clock.now(),
                input_sha256=previous.result_sha256 if previous is not None else None,
                predecessor=previous,
            )
            self.manifests.save(running)
            try:
                output = operation(request, task_name, previous)
            except SideloadedIPAError as error:
                failed = finish_stage(
                    running,
                    status=StageStatus.FAILED,
                    completed_at=self.clock.now(),
                    diagnostics=(error.to_diagnostic(),),
                )
                self.manifests.save(failed)
                documents.append(
                    {
                        "manifest": _manifest_document(failed),
                        "output": None,
                        "diagnostic": _diagnostic_document(error),
                    }
                )
                previous = failed
                for skipped_stage in stages[index + 1 :]:
                    skipped = skip_stage(
                        task_name=task_name,
                        stage=skipped_stage,
                        skipped_at=self.clock.now(),
                        predecessor=previous,
                        diagnostics=(error.to_diagnostic(),),
                    )
                    self.manifests.save(skipped)
                    documents.append(
                        {
                            "manifest": _manifest_document(skipped),
                            "output": None,
                            "diagnostic": _diagnostic_document(error),
                        }
                    )
                    previous = skipped
                human.append(f"{task_name}: failed at {stage.value} [{error.code.value}]")
                return documents, human, False

            succeeded = finish_stage(
                running,
                status=StageStatus.SUCCEEDED,
                completed_at=self.clock.now(),
                result_sha256=output.result_sha256,
            )
            self.manifests.save(succeeded)
            documents.append(
                {
                    "manifest": _manifest_document(succeeded),
                    "output": {key: thaw_json(value) for key, value in output.payload},
                    "diagnostic": None,
                }
            )
            if output.human_output is not None:
                human.append(output.human_output)
            previous = succeeded
        return documents, human, True

    def _execute(
        self,
        request: CommandRequest,
        stages: tuple[PipelineStage, ...],
    ) -> CommandResult:
        task_documents: list[dict[str, object]] = []
        human_lines: list[str] = []
        passed = True
        for task_name in self.select_tasks(request):
            predecessor = self._predecessor(request, task_name)
            documents, human, task_passed = self._execute_task(
                request,
                task_name,
                stages,
                predecessor,
            )
            task_documents.append(
                {"task_name": task_name, "passed": task_passed, "stages": documents}
            )
            human_lines.extend(human)
            passed = passed and task_passed
        document = {
            "schema_version": 1,
            "command": request.command.value,
            "passed": passed,
            "tasks": task_documents,
        }
        frozen = freeze_json(document)
        if not isinstance(frozen, FrozenJsonObject):
            raise TypeError("pipeline command report root must be an object")
        return CommandResult(
            exit_code=0 if passed else 1,
            human_output="\n".join(human_lines),
            payload=frozen.items,
        )

    def inspect(self, request: CommandRequest) -> CommandResult:
        return self._execute(request, _COMMAND_STAGES["inspect"])

    def plan(self, request: CommandRequest) -> CommandResult:
        return self._execute(request, _COMMAND_STAGES["plan"])

    def sync(self, request: CommandRequest) -> CommandResult:
        return self._execute(request, _COMMAND_STAGES["sync"])

    def sign(self, request: CommandRequest) -> CommandResult:
        return self._execute(request, _COMMAND_STAGES["sign"])

    def verify(self, request: CommandRequest) -> CommandResult:
        return self._execute(request, _COMMAND_STAGES["verify"])

    def run(self, request: CommandRequest) -> CommandResult:
        stages = tuple(PipelineStage)
        if not request.publish:
            stages = stages[: stages.index(PipelineStage.PUBLISH)]
        return self._execute(request, stages)

    def application(self) -> Application:
        return Application(
            inspect=self.inspect,
            plan=self.plan,
            sync=self.sync,
            sign=self.sign,
            verify=self.verify,
            run=self.run,
        )
