"""Pipeline-stage and publication result values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

from sideloadedipa.domain.common import Diagnostic
from sideloadedipa.domain.signing import SigningPlan


class PipelineStage(StrEnum):
    SOURCE = "source"
    INVENTORY = "inventory"
    POLICY = "policy"
    RESOURCE_PLAN = "resource-plan"
    RESOURCE_APPLY = "resource-apply"
    SIGNING_PLAN = "signing-plan"
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
class StageManifest:
    schema_version: int
    task_name: str
    stage: PipelineStage
    status: StageStatus
    input_sha256: str | None
    predecessor_sha256: str | None
    result_sha256: str | None
    started_at: datetime
    completed_at: datetime | None
    diagnostics: tuple[Diagnostic, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class PublicationResult:
    task_name: str
    artifact_key: str
    artifact_url: str
    artifact_sha256: str
    registry_key: str
    registry_sha256: str
    stale_keys_removed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceAsset:
    asset_id: str
    name: str
    source_url: str
    version: str
    published_at: datetime | None
    path: PurePosixPath
    sha256: str


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    node_path: PurePosixPath
    check: str
    passed: bool
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class VerificationResult:
    plan_sha256: str
    artifact_sha256: str
    passed: bool
    findings: tuple[VerificationFinding, ...]
    report_sha256: str


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    key: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class PublicationCandidate:
    task_name: str
    slug: str
    app_name: str
    bundle_id: str
    version: str
    filename: str
    artifact_path: str
    artifact_sha256: str
    icon_url: str | None
    publication_enabled: bool
    plan: SigningPlan
    verification: VerificationResult
