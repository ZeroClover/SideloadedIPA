"""Ordered atomic stage evidence shared by concrete transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sideloadedipa.domain.pipeline import PipelineStage, StageManifest, StageStatus
from sideloadedipa.errors import DomainError, ErrorCode, SideloadedIPAError
from sideloadedipa.pipeline.manifest_store import FileStageManifestStore
from sideloadedipa.pipeline.stage_manifests import finish_stage, start_stage


@dataclass(frozen=True, slots=True)
class StageEvidence:
    root: Path
    clock: Callable[[], datetime]

    def store(self, run_id: str) -> FileStageManifestStore:
        return FileStageManifestStore(self.root, run_id)

    def record_success(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
        result_sha256: str,
        predecessor: StageManifest | None,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> StageManifest:
        existing = store.load(task_name, stage)
        if existing is not None:
            if (
                existing.status is StageStatus.SUCCEEDED
                and existing.result_sha256 == result_sha256
                and existing.predecessor_sha256
                == (predecessor.manifest_sha256 if predecessor is not None else None)
            ):
                return existing
            raise DomainError(
                ErrorCode.PIPELINE_TRANSITION_INVALID,
                "existing stage evidence differs from current inputs",
                task_name=task_name,
                remediation="use a new run ID after changing pipeline inputs",
                safe_details=(("stage", stage.value),),
            )
        running = start_stage(
            task_name=task_name,
            stage=stage,
            started_at=started_at or self.clock(),
            input_sha256=predecessor.result_sha256 if predecessor is not None else None,
            predecessor=predecessor,
        )
        store.save(running)
        completed = finish_stage(
            running,
            status=StageStatus.SUCCEEDED,
            completed_at=completed_at or self.clock(),
            result_sha256=result_sha256,
        )
        store.save(completed)
        return completed

    def record_failure(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
        error: SideloadedIPAError,
        predecessor: StageManifest | None,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        if store.load(task_name, stage) is not None:
            return
        running = start_stage(
            task_name=task_name,
            stage=stage,
            started_at=started_at or self.clock(),
            input_sha256=predecessor.result_sha256 if predecessor is not None else None,
            predecessor=predecessor,
        )
        store.save(running)
        store.save(
            finish_stage(
                running,
                status=StageStatus.FAILED,
                completed_at=completed_at or self.clock(),
                diagnostics=(error.to_diagnostic(),),
            )
        )

    def require(
        self,
        store: FileStageManifestStore,
        task_name: str,
        stage: PipelineStage,
    ) -> StageManifest:
        manifest = store.load(task_name, stage)
        if manifest is None or manifest.status is not StageStatus.SUCCEEDED:
            raise DomainError(
                ErrorCode.PIPELINE_TRANSITION_INVALID,
                "required production predecessor manifest is missing or unsuccessful",
                task_name=task_name,
                remediation=f"complete the {stage.value} stage for this run first",
                safe_details=(("stage", stage.value),),
            )
        store.completed(task_name)
        return manifest
