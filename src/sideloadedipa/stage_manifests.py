"""Canonical pipeline-stage manifests and ordered transitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime

from sideloadedipa.domain import (
    Diagnostic,
    PipelineStage,
    StageManifest,
    StageStatus,
    thaw_json,
)
from sideloadedipa.errors import DomainError, ErrorCode

STAGE_MANIFEST_SCHEMA_VERSION = 1
PIPELINE_STAGE_ORDER = (
    PipelineStage.SOURCE,
    PipelineStage.INVENTORY,
    PipelineStage.POLICY,
    PipelineStage.RESOURCE_PLAN,
    PipelineStage.RESOURCE_APPLY,
    PipelineStage.SIGNING_PLAN,
    PipelineStage.SIGN,
    PipelineStage.VERIFY,
    PipelineStage.PUBLISH,
)


def _diagnostic_document(diagnostic: Diagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
        "task_name": diagnostic.task_name,
        "bundle_id": diagnostic.bundle_id,
        "remediation": diagnostic.remediation,
        "details": {key: thaw_json(value) for key, value in diagnostic.details},
    }


def _document(manifest: StageManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "task_name": manifest.task_name,
        "stage": manifest.stage.value,
        "status": manifest.status.value,
        "input_sha256": manifest.input_sha256,
        "predecessor_sha256": manifest.predecessor_sha256,
        "result_sha256": manifest.result_sha256,
        "started_at": manifest.started_at.isoformat(),
        "completed_at": (
            manifest.completed_at.isoformat() if manifest.completed_at is not None else None
        ),
        "diagnostics": [_diagnostic_document(value) for value in manifest.diagnostics],
    }


def _canonical_json(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def stage_manifest_sha256(manifest: StageManifest) -> str:
    return hashlib.sha256(_canonical_json(_document(manifest))).hexdigest()


def canonical_stage_manifest_json(manifest: StageManifest) -> bytes:
    if manifest.manifest_sha256 != stage_manifest_sha256(manifest):
        raise ValueError("stage manifest digest is inconsistent with its contents")
    document = _document(manifest)
    document["manifest_sha256"] = manifest.manifest_sha256
    return _canonical_json(document)


def _transition_error(
    task_name: str,
    message: str,
    *,
    stage: PipelineStage,
) -> DomainError:
    return DomainError(
        ErrorCode.PIPELINE_TRANSITION_INVALID,
        message,
        task_name=task_name,
        remediation="restart from the first incomplete pipeline stage",
        safe_details=(("stage", stage.value),),
    )


def _next_stage(stage: PipelineStage) -> PipelineStage | None:
    index = PIPELINE_STAGE_ORDER.index(stage)
    return PIPELINE_STAGE_ORDER[index + 1] if index + 1 < len(PIPELINE_STAGE_ORDER) else None


def _with_digest(manifest: StageManifest) -> StageManifest:
    return replace(manifest, manifest_sha256=stage_manifest_sha256(manifest))


def start_stage(
    *,
    task_name: str,
    stage: PipelineStage,
    started_at: datetime,
    input_sha256: str | None,
    predecessor: StageManifest | None = None,
) -> StageManifest:
    """Start only the first stage or the exact successor of a successful manifest."""

    if predecessor is None:
        if stage is not PIPELINE_STAGE_ORDER[0]:
            raise _transition_error(
                task_name, "pipeline must start at the source stage", stage=stage
            )
        predecessor_sha256 = None
    else:
        if predecessor.task_name != task_name:
            raise _transition_error(task_name, "predecessor belongs to another task", stage=stage)
        if predecessor.manifest_sha256 != stage_manifest_sha256(predecessor):
            raise _transition_error(
                task_name, "predecessor manifest digest is invalid", stage=stage
            )
        if predecessor.status is not StageStatus.SUCCEEDED:
            raise _transition_error(task_name, "predecessor stage did not succeed", stage=stage)
        if _next_stage(predecessor.stage) is not stage:
            raise _transition_error(task_name, "pipeline stage is out of order", stage=stage)
        predecessor_sha256 = predecessor.manifest_sha256

    return _with_digest(
        StageManifest(
            STAGE_MANIFEST_SCHEMA_VERSION,
            task_name,
            stage,
            StageStatus.RUNNING,
            input_sha256,
            predecessor_sha256,
            None,
            started_at,
            None,
            (),
            "",
        )
    )


def finish_stage(
    manifest: StageManifest,
    *,
    status: StageStatus,
    completed_at: datetime,
    result_sha256: str | None = None,
    diagnostics: tuple[Diagnostic, ...] = (),
) -> StageManifest:
    """Finish a running stage exactly once as succeeded or failed."""

    if manifest.status is not StageStatus.RUNNING:
        raise _transition_error(
            manifest.task_name,
            "only a running stage can be completed",
            stage=manifest.stage,
        )
    if status not in {StageStatus.SUCCEEDED, StageStatus.FAILED}:
        raise _transition_error(
            manifest.task_name,
            "a running stage must finish as succeeded or failed",
            stage=manifest.stage,
        )
    if status is StageStatus.SUCCEEDED and result_sha256 is None:
        raise _transition_error(
            manifest.task_name,
            "a successful stage requires a result digest",
            stage=manifest.stage,
        )
    return _with_digest(
        replace(
            manifest,
            status=status,
            result_sha256=result_sha256,
            completed_at=completed_at,
            diagnostics=diagnostics,
            manifest_sha256="",
        )
    )


def skip_stage(
    *,
    task_name: str,
    stage: PipelineStage,
    skipped_at: datetime,
    predecessor: StageManifest,
    diagnostics: tuple[Diagnostic, ...] = (),
) -> StageManifest:
    """Record the exact successor as skipped after a failed or skipped stage."""

    if predecessor.task_name != task_name or _next_stage(predecessor.stage) is not stage:
        raise _transition_error(task_name, "skipped pipeline stage is out of order", stage=stage)
    if predecessor.status not in {StageStatus.FAILED, StageStatus.SKIPPED}:
        raise _transition_error(
            task_name,
            "stages can be skipped only after a failed or skipped predecessor",
            stage=stage,
        )
    if predecessor.manifest_sha256 != stage_manifest_sha256(predecessor):
        raise _transition_error(task_name, "predecessor manifest digest is invalid", stage=stage)
    return _with_digest(
        StageManifest(
            STAGE_MANIFEST_SCHEMA_VERSION,
            task_name,
            stage,
            StageStatus.SKIPPED,
            None,
            predecessor.manifest_sha256,
            None,
            skipped_at,
            skipped_at,
            diagnostics,
            "",
        )
    )
