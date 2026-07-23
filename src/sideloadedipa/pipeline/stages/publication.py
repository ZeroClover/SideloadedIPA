"""Atomic publication, cache promotion, and final reporting transaction."""

from __future__ import annotations

from dataclasses import dataclass

from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.domain.config import TaskConfiguration
from sideloadedipa.domain.pipeline import PipelineStage, PublicationCandidate, VerificationResult
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.pipeline.cancellation import SideEffectJournal
from sideloadedipa.pipeline.environment import publication_runtime
from sideloadedipa.pipeline.publish_stage import build_publication_candidate
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import SourceContext
from sideloadedipa.pipeline.stages.results import command_result
from sideloadedipa.pipeline.stages.signing import PreparedFactory, SigningStage
from sideloadedipa.pipeline.stages.verification import VerificationStage
from sideloadedipa.signing.service import verify_package_artifact


@dataclass(frozen=True, slots=True)
class PublicationStage:
    signing: SigningStage
    verification: VerificationStage
    evidence: StageEvidence

    def publish(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
        configuration: TaskConfiguration,
        prepared_factory: PreparedFactory,
        journal: SideEffectJournal | None,
    ) -> CommandResult:
        store = self.evidence.store(request.run_id)
        verifications: dict[str, VerificationResult] = {}
        with prepared_factory(request, contexts) as prepared:
            publication_store, publisher = publication_runtime(
                configuration,
                self.signing.package.environment,
            )
            stage_started_at = self.evidence.clock()
            candidates: list[PublicationCandidate] = []
            for value in prepared:
                task = value.source.task
                verification_manifest = self.evidence.require(
                    store,
                    task.task_name,
                    PipelineStage.VERIFY,
                )
                if not task.publication_enabled:
                    raise ConfigurationError(
                        ErrorCode.CONFIG_INVALID,
                        "selected task is not approved for publication",
                        task_name=task.task_name,
                    )
                plan = value.plan
                verification = verify_package_artifact(
                    value.request,
                    plan,
                    value.request.destination_ipa,
                )
                if verification_manifest.result_sha256 != verification.report_sha256:
                    raise DomainError(
                        ErrorCode.PIPELINE_TRANSITION_INVALID,
                        "current verification differs from the retained verify stage",
                        task_name=task.task_name,
                    )
                verifications[task.task_name] = verification
                candidates.append(
                    build_publication_candidate(
                        task=task,
                        source=value.source.source,
                        source_evidence=value.source.resolved.evidence,
                        artifact=value.request.destination_ipa,
                        plan=plan,
                        verification=verification,
                        store=publication_store,
                    )
                )
            results = publisher.publish(candidates, now=self.evidence.clock())
            stage_completed_at = self.evidence.clock()
            if journal is not None:
                journal.mark_publication_committed()
            publications = {value.task_name: value for value in results}
            for value in prepared:
                task_name = value.source.task.task_name
                verify_manifest = self.evidence.require(
                    store,
                    task_name,
                    PipelineStage.VERIFY,
                )
                self.evidence.record_success(
                    store,
                    task_name,
                    PipelineStage.PUBLISH,
                    publications[task_name].registry_sha256,
                    verify_manifest,
                    started_at=stage_started_at,
                    completed_at=stage_completed_at,
                )
            self.signing.promote_cache(request)
            report = self.verification.write_report(
                request,
                prepared,
                verifications,
                publications,
            )
        return command_result(
            "publish",
            {
                "status": "passed",
                "report_path": str(report),
                "tasks": [
                    {
                        "task_name": value.task_name,
                        "artifact_key": value.artifact_key,
                        "registry_sha256": value.registry_sha256,
                    }
                    for value in results
                ],
            },
            f"Production publication: {len(results)} passed",
        )
