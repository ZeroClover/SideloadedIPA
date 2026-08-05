"""Atomic publication, cache promotion, and final reporting transaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from sideloadedipa.adapters.publication.icons import IconError, build_icon_png
from sideloadedipa.adapters.publication.r2_store import R2Store
from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.domain.config import Task, TaskConfiguration
from sideloadedipa.domain.pipeline import (
    PipelineStage,
    PublicationCandidate,
    SourceAsset,
    VerificationResult,
)
from sideloadedipa.domain.signing import SigningPlan
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.ipa import read_ipa_metadata
from sideloadedipa.pipeline.cancellation import SideEffectJournal
from sideloadedipa.pipeline.environment import publication_runtime, safe_filename
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import SourceContext
from sideloadedipa.pipeline.stages.results import command_result
from sideloadedipa.pipeline.stages.signing import PreparedFactory, SigningStage
from sideloadedipa.pipeline.stages.verification import VerificationStage
from sideloadedipa.util.atomics import file_sha256


def _upload_icon(
    *,
    task: Task,
    source: SourceAsset,
    source_evidence: Mapping[str, object],
    artifact: Path,
    store: R2Store,
) -> str | None:
    if task.icon_path is None:
        return None
    try:
        png = build_icon_png(
            task.icon_path,
            task.source.location,
            ref=source.version if source_evidence.get("release_tag") is not None else None,
            ipa_path=artifact,
        )
        return store.upload_icon(task.slug, png)
    except (BotoCoreError, ClientError, IconError, OSError):
        return None


def build_publication_candidate(
    *,
    task: Task,
    source: SourceAsset,
    source_evidence: Mapping[str, object],
    artifact: Path,
    plan: SigningPlan,
    verification: VerificationResult,
    store: R2Store,
) -> PublicationCandidate:
    metadata = read_ipa_metadata(artifact)
    return PublicationCandidate(
        task.task_name,
        task.slug,
        task.app_name,
        metadata.bundle_id,
        metadata.version,
        f"{safe_filename(task.app_name)}.ipa",
        str(artifact),
        file_sha256(artifact),
        _upload_icon(
            task=task,
            source=source,
            source_evidence=source_evidence,
            artifact=artifact,
            store=store,
        ),
        task.publication_enabled,
        plan,
        verification,
    )


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
                verification = self.verification.load_verification(request, plan)
                artifact_sha256 = file_sha256(value.request.destination_ipa)
                if (
                    verification_manifest.result_sha256 != verification.report_sha256
                    or verification.plan_sha256 != plan.plan_sha256
                    or verification.artifact_sha256 != artifact_sha256
                ):
                    raise DomainError(
                        ErrorCode.PIPELINE_TRANSITION_INVALID,
                        "retained verification evidence differs from the publish artifact",
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
