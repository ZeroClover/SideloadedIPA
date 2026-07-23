"""Independent signed-artifact verification and run-report transaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.apple.intents import derive_bundle_resource_intents
from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.domain.capabilities import capability_rule
from sideloadedipa.domain.pipeline import PipelineStage, PublicationResult, VerificationResult
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.pipeline.run_reports import RunReport, TaskRunEvidence, write_run_report
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import PreparedContext, SourceContext
from sideloadedipa.pipeline.stages.results import command_result
from sideloadedipa.pipeline.stages.signing import PreparedFactory, SigningStage
from sideloadedipa.signing.service import verify_package_artifact
from sideloadedipa.util.atomics import file_sha256


@dataclass(frozen=True, slots=True)
class VerificationStage:
    signing: SigningStage
    evidence: StageEvidence
    report_root: Path

    def write_report(
        self,
        request: CommandRequest,
        prepared: tuple[PreparedContext, ...],
        verifications: Mapping[str, VerificationResult],
        publications: Mapping[str, PublicationResult] | None = None,
    ) -> Path:
        decisions = {value.task_name: value for value in self.signing.read_decisions(request)}
        store = self.evidence.store(request.run_id)
        tasks = tuple(
            TaskRunEvidence(
                task_name=value.source.task.task_name,
                stages=store.completed(value.source.task.task_name),
                source=value.source.source,
                graph_sha256=value.source.graph.graph_sha256,
                plan=value.plan,
                capability_classifications=tuple(
                    (
                        intent.target_bundle_id,
                        capability,
                        capability_rule(capability).automation.value,
                    )
                    for intent in derive_bundle_resource_intents(value.source.task)
                    for capability in intent.required_capabilities
                ),
                manual_actions=(
                    value.source.task.signing.manual_app_group_associations
                    if value.source.task.signing is not None
                    else ()
                ),
                apple_resource_ids=tuple(
                    resource
                    for entry in value.request.profile_manifest.entries
                    for resource in (
                        ("bundle-id", entry.bundle_resource_id),
                        ("profile", entry.profile_resource_id),
                    )
                ),
                cache_decision=decisions[value.source.task.task_name],
                verification=verifications[value.source.task.task_name],
                publication=(
                    publications.get(value.source.task.task_name)
                    if publications is not None
                    else None
                ),
            )
            for value in prepared
        )
        started_at = min(stage.started_at for task in tasks for stage in task.stages)
        completed_at = max(
            stage.completed_at or stage.started_at for task in tasks for stage in task.stages
        )
        path = self.report_root / f"{request.run_id}.json"
        environment = self.signing.package.environment
        redactions = tuple(
            value
            for key, value in environment.items()
            if value
            and any(token in key for token in ("SECRET", "PASSWORD", "PRIVATE", "P12", "TOKEN"))
        )
        write_run_report(
            path,
            RunReport(request.run_id, started_at, completed_at, tasks),
            secret_redactions=redactions,
            path_redactions=(store.run_root,),
        )
        return path

    def verify(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
        prepared_factory: PreparedFactory,
    ) -> CommandResult:
        store = self.evidence.store(request.run_id)
        for context in contexts:
            self.evidence.require(store, context.task.task_name, PipelineStage.SIGN)
        verifications: dict[str, VerificationResult] = {}
        with prepared_factory(request, contexts) as prepared:
            for value in prepared:
                task_name = value.source.task.task_name
                stage_started_at = self.evidence.clock()
                signing = self.evidence.require(store, task_name, PipelineStage.SIGN)
                plan = value.plan
                planned = self.evidence.require(
                    store,
                    task_name,
                    PipelineStage.SIGNING_PLAN,
                )
                if planned.result_sha256 != plan.plan_sha256:
                    raise DomainError(
                        ErrorCode.PIPELINE_TRANSITION_INVALID,
                        "reconstructed signing plan differs from the sign stage",
                        task_name=task_name,
                    )
                if signing.result_sha256 != file_sha256(value.request.destination_ipa):
                    raise DomainError(
                        ErrorCode.SIGNING_VERIFICATION_FAILED,
                        "signed artifact changed before standalone verification",
                        task_name=task_name,
                    )
                verification = verify_package_artifact(
                    value.request,
                    plan,
                    value.request.destination_ipa,
                )
                verifications[task_name] = verification
                self.evidence.record_success(
                    store,
                    task_name,
                    PipelineStage.VERIFY,
                    verification.report_sha256,
                    signing,
                    started_at=stage_started_at,
                )
            if not request.publish:
                self.signing.promote_cache(request)
            report = self.write_report(request, prepared, verifications)
        return command_result(
            "verify",
            {
                "status": "passed",
                "report_path": str(report),
                "tasks": [
                    {
                        "task_name": task_name,
                        "verification_report_sha256": verification.report_sha256,
                    }
                    for task_name, verification in verifications.items()
                ],
            },
            f"Production verification: {len(verifications)} passed",
        )
