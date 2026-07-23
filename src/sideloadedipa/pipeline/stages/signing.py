"""Signing preparation, cache decision, and signing transaction."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sideloadedipa.application import CommandRequest, CommandResult
from sideloadedipa.cache.decisions import (
    RebuildDecision,
    RebuildReason,
    TaskCacheRecord,
    build_cache_index,
    select_rebuilds,
)
from sideloadedipa.cache.reuse import CachePrerequisiteState, revalidate_cached_artifact
from sideloadedipa.cache.store import SigningCacheStore
from sideloadedipa.domain.pipeline import PipelineStage, VerificationResult
from sideloadedipa.domain.signing import SigningPlan
from sideloadedipa.errors import ConfigurationError, ErrorCode, SideloadedIPAError
from sideloadedipa.pipeline.environment import (
    PipelineEnvironmentDependencies,
    decode_p12,
    required_environment,
)
from sideloadedipa.pipeline.package_runner import prepare_package_signing
from sideloadedipa.pipeline.sign_stage import (
    build_fingerprint,
    restore_cached_signing_report,
)
from sideloadedipa.pipeline.stages.evidence import StageEvidence
from sideloadedipa.pipeline.stages.models import PreparedContext, SourceContext
from sideloadedipa.pipeline.stages.results import command_result
from sideloadedipa.signing.reports import canonical_signing_report_json
from sideloadedipa.signing.service import execute_package_signing
from sideloadedipa.util.atomics import (
    atomic_copy,
    atomic_write_bytes,
    canonical_json,
    file_sha256,
)

_PROFILE_REFRESH_THRESHOLD = timedelta(days=30)
PreparedFactory = Callable[
    [CommandRequest, tuple[SourceContext, ...]],
    AbstractContextManager[tuple[PreparedContext, ...]],
]


@dataclass(frozen=True, slots=True)
class SigningStage:
    package: PipelineEnvironmentDependencies
    evidence: StageEvidence

    def cache(self) -> SigningCacheStore:
        return SigningCacheStore(self.package.cache_root)

    def pending_cache(self, request: CommandRequest) -> SigningCacheStore:
        return SigningCacheStore(self.evidence.store(request.run_id).run_root / "pending-cache")

    def signing_report_path(self, request: CommandRequest, task_name: str) -> Path:
        return self.evidence.store(request.run_id).task_root(task_name) / "signing-report.json"

    def require_environment(self) -> None:
        for key in (
            "ZSIGN_BIN",
            "ZSIGN_SHA256",
            "APPLE_DEV_CERT_P12_ENCODED",
            "APPLE_DEV_CERT_PASSWORD",
        ):
            required_environment(self.package.environment, key)

    @contextmanager
    def prepared(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
    ) -> Iterator[tuple[PreparedContext, ...]]:
        environment = self.package.environment
        zsign = Path(required_environment(environment, "ZSIGN_BIN"))
        zsign_sha256 = required_environment(environment, "ZSIGN_SHA256")
        repository_root = request.config_path.resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="sideloadedipa-production-") as directory:
            private_root = Path(directory)
            p12_path = private_root / "certificate.p12"
            p12_password = decode_p12(environment, p12_path)
            prepared: list[PreparedContext] = []
            for context in contexts:
                destination = self.package.output_root / f"{context.task.slug}.ipa"
                signing_request = prepare_package_signing(
                    task=context.task,
                    source_ipa=context.downloaded.path,
                    destination_ipa=destination,
                    profile_root=self.package.profile_root,
                    p12_path=p12_path,
                    p12_password=p12_password,
                    private_directory=private_root / context.task.slug,
                    zsign_executable=zsign,
                    zsign_sha256=zsign_sha256,
                    repository_root=repository_root,
                    graph=context.graph,
                )
                prepared.append(
                    PreparedContext(
                        context,
                        signing_request,
                        build_fingerprint(
                            task=context.task,
                            source_asset=context.source,
                            graph=context.graph,
                            request=signing_request,
                            repository_root=repository_root,
                        ),
                    )
                )
            yield tuple(prepared)

    def write_decisions(
        self,
        request: CommandRequest,
        decisions: tuple[RebuildDecision, ...],
    ) -> None:
        path = self.evidence.store(request.run_id).run_root / "cache-decisions.json"
        atomic_write_bytes(
            path,
            canonical_json(
                [
                    {
                        "task_name": value.task_name,
                        "rebuild": value.rebuild,
                        "reason": value.reason.value,
                        "fingerprint_sha256": value.fingerprint_sha256,
                        "cached_artifact_sha256": value.cached_artifact_sha256,
                    }
                    for value in decisions
                ]
            )
            + b"\n",
        )

    def read_decisions(self, request: CommandRequest) -> tuple[RebuildDecision, ...]:
        path = self.evidence.store(request.run_id).run_root / "cache-decisions.json"
        try:
            values = json.loads(path.read_text())
            return tuple(
                RebuildDecision(
                    value["task_name"],
                    value["rebuild"],
                    RebuildReason(value["reason"]),
                    value["fingerprint_sha256"],
                    value["cached_artifact_sha256"],
                )
                for value in values
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "production cache decisions are missing or invalid",
                remediation="rerun the sign stage for this run ID",
            ) from error

    def _execute_and_cache(
        self,
        request: CommandRequest,
        value: PreparedContext,
        plan: SigningPlan,
    ) -> tuple[VerificationResult, str]:
        task_name = value.source.task.task_name
        execution = execute_package_signing(value.request)
        artifact = self.cache().artifact_path(task_name, value.fingerprint.sha256)
        atomic_copy(value.request.destination_ipa, artifact)
        signing_report = canonical_signing_report_json(plan, execution.execution.signing)
        signing_report_sha256 = hashlib.sha256(signing_report).hexdigest()
        atomic_write_bytes(
            self.cache().signing_report_path(task_name, value.fingerprint.sha256),
            signing_report,
        )
        atomic_write_bytes(self.signing_report_path(request, task_name), signing_report)
        return execution.execution.verification, signing_report_sha256

    def sign(
        self,
        request: CommandRequest,
        contexts: tuple[SourceContext, ...],
        prepared_factory: PreparedFactory,
    ) -> CommandResult:
        store = self.evidence.store(request.run_id)
        for context in contexts:
            self.evidence.require(store, context.task.task_name, PipelineStage.RESOURCE_APPLY)
        try:
            cached = self.cache().load()
        except ValueError:
            cached = None
        with prepared_factory(request, contexts) as prepared:
            decisions = list(
                select_rebuilds(
                    tuple(value.fingerprint for value in prepared),
                    cached,
                    force=request.force_rebuild,
                )
            )
            cached_records = {value.task_name: value for value in cached.records} if cached else {}
            pending_records: list[TaskCacheRecord] = []
            for index, value in enumerate(prepared):
                task_name = value.source.task.task_name
                resource_apply = self.evidence.require(
                    store,
                    task_name,
                    PipelineStage.RESOURCE_APPLY,
                )
                plan_started_at = self.evidence.clock()
                plan = value.plan
                signing_plan = self.evidence.record_success(
                    store,
                    task_name,
                    PipelineStage.SIGNING_PLAN,
                    plan.plan_sha256,
                    resource_apply,
                    started_at=plan_started_at,
                )
                decision = decisions[index]
                verification: VerificationResult
                signing_report_sha256: str
                sign_started_at = self.evidence.clock()
                if not decision.rebuild:
                    record = cached_records[task_name]
                    artifact = self.cache().artifact_path(task_name, value.fingerprint.sha256)
                    try:
                        verification = revalidate_cached_artifact(
                            plan=plan,
                            cache_record=record,
                            artifact=artifact,
                            prerequisites=CachePrerequisiteState(
                                True,
                                value.request.profile_manifest.snapshot_sha256,
                            ),
                            profiles=value.request.profiles,
                            now=self.evidence.clock(),
                            refresh_threshold=_PROFILE_REFRESH_THRESHOLD,
                            verifier=value.request.verifier,
                        )
                        signing_report_sha256 = restore_cached_signing_report(
                            plan=plan,
                            record=record,
                            cached_path=self.cache().signing_report_path(
                                task_name,
                                value.fingerprint.sha256,
                            ),
                            retained_path=self.signing_report_path(request, task_name),
                        )
                        atomic_copy(artifact, value.request.destination_ipa)
                    except (OSError, SideloadedIPAError):
                        decision = RebuildDecision(
                            task_name,
                            True,
                            RebuildReason.CACHE_REJECTED,
                            value.fingerprint.sha256,
                            record.artifact_sha256,
                        )
                        decisions[index] = decision
                        verification, signing_report_sha256 = self._execute_and_cache(
                            request,
                            value,
                            plan,
                        )
                else:
                    verification, signing_report_sha256 = self._execute_and_cache(
                        request,
                        value,
                        plan,
                    )
                artifact_sha256 = file_sha256(value.request.destination_ipa)
                self.evidence.record_success(
                    store,
                    task_name,
                    PipelineStage.SIGN,
                    artifact_sha256,
                    signing_plan,
                    started_at=sign_started_at,
                )
                pending_records.append(
                    TaskCacheRecord(
                        task_name,
                        value.fingerprint.schema_version,
                        value.fingerprint.sha256,
                        artifact_sha256,
                        verification.report_sha256,
                        signing_report_sha256,
                    )
                )
            decisions_tuple = tuple(decisions)
            self.write_decisions(request, decisions_tuple)
            existing = (
                {record.task_name: record for record in cached.records}
                if cached is not None
                else {}
            )
            existing.update({record.task_name: record for record in pending_records})
            self.pending_cache(request).save(build_cache_index(tuple(existing.values())))
        return command_result(
            "sign",
            {
                "status": "passed",
                "tasks": [
                    {
                        "task_name": value.task_name,
                        "rebuild": value.rebuild,
                        "reason": value.reason.value,
                        "signing_report_sha256": next(
                            record.signing_report_sha256
                            for record in pending_records
                            if record.task_name == value.task_name
                        ),
                    }
                    for value in decisions_tuple
                ],
            },
            f"Production signing: {len(decisions_tuple)} passed",
        )

    def promote_cache(self, request: CommandRequest) -> None:
        pending = self.pending_cache(request).load()
        if pending is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_MISSING,
                "verified run has no pending cache index",
                remediation="rerun signing and verification for this run ID",
            )
        self.cache().save(pending)
