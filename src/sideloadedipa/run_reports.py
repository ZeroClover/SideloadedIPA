"""Schema-versioned, redacted reports for complete pipeline runs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sideloadedipa.cache_decisions import RebuildDecision
from sideloadedipa.domain import (
    PublicationResult,
    SigningPlan,
    SourceAsset,
    StageManifest,
    StageStatus,
    VerificationResult,
)
from sideloadedipa.stage_manifests import canonical_stage_manifest_json

RUN_REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TaskRunEvidence:
    task_name: str
    stages: tuple[StageManifest, ...]
    source: SourceAsset | None = None
    graph_sha256: str | None = None
    plan: SigningPlan | None = None
    capability_classifications: tuple[tuple[str, str, str], ...] = ()
    manual_actions: tuple[str, ...] = ()
    apple_resource_ids: tuple[tuple[str, str], ...] = ()
    cache_decision: RebuildDecision | None = None
    verification: VerificationResult | None = None
    publication: PublicationResult | None = None


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: str
    started_at: datetime
    completed_at: datetime
    tasks: tuple[TaskRunEvidence, ...]


def _redact_text(value: str, redactions: Sequence[str]) -> str:
    for secret in sorted((item for item in redactions if item), key=len, reverse=True):
        value = value.replace(secret, "***")
    return value


def _redact(value: object, redactions: Sequence[str]) -> object:
    if isinstance(value, str):
        return _redact_text(value, redactions)
    if isinstance(value, list):
        return [_redact(item, redactions) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, redactions) for key, item in value.items()}
    return value


def _stage_document(stage: StageManifest) -> dict[str, object]:
    canonical_stage_manifest_json(stage)
    duration = (
        (stage.completed_at - stage.started_at).total_seconds()
        if stage.completed_at is not None
        else None
    )
    if duration is not None and duration < 0:
        raise ValueError("stage completion precedes its start")
    return {
        "stage": stage.stage.value,
        "status": stage.status.value,
        "duration_seconds": duration,
        "result_sha256": stage.result_sha256,
        "manifest_sha256": stage.manifest_sha256,
        "diagnostic_codes": [value.code for value in stage.diagnostics],
    }


def _source_document(source: SourceAsset | None) -> dict[str, object] | None:
    if source is None:
        return None
    return {
        "asset_id": source.asset_id,
        "name": source.name,
        "version": source.version,
        "published_at": source.published_at.isoformat() if source.published_at else None,
        "sha256": source.sha256,
    }


def _plan_document(plan: SigningPlan | None) -> dict[str, object] | None:
    if plan is None:
        return None
    return {
        "plan_sha256": plan.plan_sha256,
        "graph_sha256": plan.graph_sha256,
        "certificate_sha256": plan.certificate_sha256,
        "backend": {
            "name": plan.backend.name,
            "version": plan.backend.version,
            "executable_sha256": plan.backend.executable_sha256,
            "contract_version": plan.backend.contract_version,
        },
        "bundles": [
            {
                "source_path": node.source_path.as_posix(),
                "target_bundle_id": node.target_bundle_id,
                "profile_resource_id": node.profile_resource_id,
                "profile_sha256": node.profile_sha256,
                "entitlements_sha256": node.expected_entitlements_sha256,
            }
            for node in plan.nodes
        ],
    }


def _verification_document(
    verification: VerificationResult | None,
) -> dict[str, object] | None:
    if verification is None:
        return None
    return {
        "passed": verification.passed,
        "plan_sha256": verification.plan_sha256,
        "artifact_sha256": verification.artifact_sha256,
        "report_sha256": verification.report_sha256,
        "findings": [
            {
                "node_path": finding.node_path.as_posix(),
                "check": finding.check,
                "passed": finding.passed,
                "expected_sha256": finding.expected_sha256,
                "actual_sha256": finding.actual_sha256,
                "diagnostic_codes": [value.code for value in finding.diagnostics],
            }
            for finding in verification.findings
        ],
    }


def _publication_document(publication: PublicationResult | None) -> dict[str, object] | None:
    if publication is None:
        return None
    return {
        "artifact_key": publication.artifact_key,
        "artifact_url": publication.artifact_url,
        "artifact_sha256": publication.artifact_sha256,
        "registry_key": publication.registry_key,
        "registry_sha256": publication.registry_sha256,
        "stale_keys_removed": list(publication.stale_keys_removed),
    }


def _task_document(evidence: TaskRunEvidence) -> dict[str, object]:
    if any(stage.task_name != evidence.task_name for stage in evidence.stages):
        raise ValueError("stage manifest belongs to another task")
    if evidence.plan is not None and evidence.plan.task_name != evidence.task_name:
        raise ValueError("signing plan belongs to another task")
    if evidence.verification is not None and (
        evidence.plan is None or evidence.verification.plan_sha256 != evidence.plan.plan_sha256
    ):
        raise ValueError("verification evidence does not match the task plan")
    if evidence.publication is not None and (
        evidence.verification is None
        or evidence.publication.artifact_sha256 != evidence.verification.artifact_sha256
    ):
        raise ValueError("publication evidence does not match verification")
    cache = evidence.cache_decision
    return {
        "task_name": evidence.task_name,
        "status": (
            evidence.stages[-1].status.value if evidence.stages else StageStatus.PENDING.value
        ),
        "stages": [_stage_document(value) for value in evidence.stages],
        "source": _source_document(evidence.source),
        "graph_sha256": evidence.graph_sha256,
        "signing_plan": _plan_document(evidence.plan),
        "capability_classifications": [
            {"bundle_id": bundle_id, "capability": capability, "classification": classification}
            for bundle_id, capability, classification in evidence.capability_classifications
        ],
        "manual_actions": list(evidence.manual_actions),
        "apple_resource_ids": [
            {"kind": kind, "resource_id": resource_id}
            for kind, resource_id in evidence.apple_resource_ids
        ],
        "cache": (
            {
                "rebuild": cache.rebuild,
                "reason": cache.reason.value,
                "fingerprint_sha256": cache.fingerprint_sha256,
                "cached_artifact_sha256": cache.cached_artifact_sha256,
            }
            if cache is not None
            else None
        ),
        "verification": _verification_document(evidence.verification),
        "publication": _publication_document(evidence.publication),
    }


def _canonical_json(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def canonical_run_report_json(
    report: RunReport,
    *,
    secret_redactions: Sequence[str] = (),
    path_redactions: Sequence[Path] = (),
) -> bytes:
    if not report.run_id or report.completed_at < report.started_at:
        raise ValueError("run report identity or timing is invalid")
    if len({task.task_name for task in report.tasks}) != len(report.tasks):
        raise ValueError("run report contains duplicate tasks")
    redactions = (*secret_redactions, *(os.fspath(path) for path in path_redactions))
    tasks = [_task_document(task) for task in report.tasks]
    document: dict[str, object] = {
        "schema_version": RUN_REPORT_SCHEMA_VERSION,
        "run_id": report.run_id,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat(),
        "duration_seconds": (report.completed_at - report.started_at).total_seconds(),
        "passed": all(
            task.stages and task.stages[-1].status is StageStatus.SUCCEEDED for task in report.tasks
        ),
        "tasks": tasks,
    }
    redacted = _redact(document, redactions)
    if not isinstance(redacted, dict):
        raise AssertionError("run report root must remain an object")
    digest = hashlib.sha256(_canonical_json(redacted)).hexdigest()
    redacted["report_sha256"] = digest
    return _canonical_json(redacted)


def write_run_report(
    path: Path,
    report: RunReport,
    *,
    secret_redactions: Sequence[str] = (),
    path_redactions: Sequence[Path] = (),
) -> None:
    payload = canonical_run_report_json(
        report,
        secret_redactions=secret_redactions,
        path_redactions=path_redactions,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def human_run_report(report: RunReport) -> str:
    duration = (report.completed_at - report.started_at).total_seconds()
    lines = [f"Run {report.run_id}: {len(report.tasks)} task(s), {duration:.2f}s"]
    for task in report.tasks:
        status = task.stages[-1].status.value if task.stages else StageStatus.PENDING.value
        cache = task.cache_decision.reason.value if task.cache_decision else "not-evaluated"
        publication = "published" if task.publication is not None else "not-published"
        lines.append(f"{task.task_name}: {status}; cache={cache}; {publication}")
    return "\n".join(lines)
