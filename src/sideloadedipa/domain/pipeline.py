"""Pipeline-stage and publication result values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sideloadedipa.domain.common import Diagnostic


class PipelineStage(StrEnum):
    INSPECT = "inspect"
    PLAN = "plan"
    SYNC = "sync"
    SIGN = "sign"
    VERIFY = "verify"
    PUBLISH = "publish"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class StageState:
    stage: PipelineStage
    status: StageStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_sha256: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PublicationResult:
    task_name: str
    artifact_key: str
    artifact_url: str
    artifact_sha256: str
    registry_key: str
    registry_sha256: str
    stale_keys_removed: tuple[str, ...] = ()
