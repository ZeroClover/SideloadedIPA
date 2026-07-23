"""Apple resource planning and synchronization transactions."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sideloadedipa.apple.commands import AppleCommandDependencies
from sideloadedipa.apple.commands import plan_command as apple_plan_command
from sideloadedipa.apple.commands import sync_command as apple_sync_command
from sideloadedipa.application import CommandName, CommandRequest, CommandResult
from sideloadedipa.domain.pipeline import PipelineStage
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.pipeline.cancellation import SideEffectJournal
from sideloadedipa.pipeline.sign_stage import json_digest
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import SourceContext
from sideloadedipa.pipeline.stages.results import payload_document


@dataclass(frozen=True, slots=True)
class AppleStage:
    dependencies: AppleCommandDependencies
    evidence: StageEvidence

    def plan(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
    ) -> CommandResult:
        store = self.evidence.store(request.run_id)
        apple_request = replace(
            request,
            command=CommandName.PLAN,
            apply=False,
            publish=False,
        )
        stage_started_at = self.evidence.clock()
        result = apple_plan_command(apple_request, self.dependencies)
        if result.exit_code:
            raise DomainError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "Apple resource plan contains blocking prerequisites",
                remediation="complete the manual or blocked operations before apply",
            )
        digest = json_digest(payload_document(result))
        stage_completed_at = self.evidence.clock()
        for context in contexts:
            policy = self.evidence.require(store, context.task.task_name, PipelineStage.POLICY)
            self.evidence.record_success(
                store,
                context.task.task_name,
                PipelineStage.RESOURCE_PLAN,
                json_digest({"task": context.task.task_name, "plan": digest}),
                policy,
                started_at=stage_started_at,
                completed_at=stage_completed_at,
            )
        return result

    def sync(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
        journal: SideEffectJournal | None,
    ) -> CommandResult:
        store = self.evidence.store(request.run_id)
        for context in contexts:
            self.evidence.require(store, context.task.task_name, PipelineStage.RESOURCE_PLAN)
        apple_request = replace(
            request,
            command=CommandName.SYNC,
            publish=False,
        )
        dependencies = self.dependencies
        if journal is not None:
            dependencies = replace(
                dependencies,
                record_created_resource=journal.record_apple_resource,
            )
        stage_started_at = self.evidence.clock()
        result = apple_sync_command(apple_request, dependencies)
        if result.exit_code:
            raise DomainError(
                ErrorCode.APPLE_RESOURCE_CONFLICT,
                "Apple resource synchronization did not reach an applied state",
            )
        digest = json_digest(payload_document(result))
        stage_completed_at = self.evidence.clock()
        for context in contexts:
            predecessor = self.evidence.require(
                store,
                context.task.task_name,
                PipelineStage.RESOURCE_PLAN,
            )
            self.evidence.record_success(
                store,
                context.task.task_name,
                PipelineStage.RESOURCE_APPLY,
                json_digest({"task": context.task.task_name, "apply": digest}),
                predecessor,
                started_at=stage_started_at,
                completed_at=stage_completed_at,
            )
        return result
