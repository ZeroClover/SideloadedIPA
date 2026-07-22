"""Atomic filesystem storage for redacted pipeline-stage manifests."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.domain import PipelineStage, StageManifest
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.pipeline.stage_manifests import (
    PIPELINE_STAGE_ORDER,
    canonical_stage_manifest_json,
    parse_stage_manifest_json,
)
from sideloadedipa.util.atomics import atomic_write_bytes


def _path_component(value: str) -> str:
    if not value:
        raise ConfigurationError(ErrorCode.CONFIG_INVALID, "pipeline identity is empty")
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")[:48] or "value"
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class FileStageManifestStore:
    root: Path
    run_id: str = "local"

    @property
    def run_root(self) -> Path:
        return self.root / _path_component(self.run_id)

    def task_root(self, task_name: str) -> Path:
        return self.run_root / _path_component(task_name)

    def path(self, task_name: str, stage: PipelineStage) -> Path:
        index = PIPELINE_STAGE_ORDER.index(stage)
        return self.task_root(task_name) / f"{index:02d}-{stage.value}.json"

    def load(self, task_name: str, stage: PipelineStage) -> StageManifest | None:
        path = self.path(task_name, stage)
        if not path.exists():
            return None
        manifest = parse_stage_manifest_json(path.read_bytes())
        if manifest.task_name != task_name or manifest.stage is not stage:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "stage manifest storage identity does not match its contents",
                task_name=task_name,
                remediation="discard the manifest and restart from the source stage",
                safe_details=(("stage", stage.value),),
            )
        return manifest

    def save(self, manifest: StageManifest) -> None:
        path = self.path(manifest.task_name, manifest.stage)
        atomic_write_bytes(path, canonical_stage_manifest_json(manifest))

    def completed(self, task_name: str) -> tuple[StageManifest, ...]:
        manifests: list[StageManifest] = []
        predecessor_sha256: str | None = None
        for index, stage in enumerate(PIPELINE_STAGE_ORDER):
            manifest = self.load(task_name, stage)
            if manifest is None:
                if any(
                    self.path(task_name, later).exists()
                    for later in PIPELINE_STAGE_ORDER[index + 1 :]
                ):
                    raise ConfigurationError(
                        ErrorCode.CONFIG_INVALID,
                        "stored stage manifest predecessor chain is invalid",
                        task_name=task_name,
                        safe_details=(("stage", stage.value),),
                    )
                break
            if manifest.predecessor_sha256 != predecessor_sha256:
                raise ConfigurationError(
                    ErrorCode.CONFIG_INVALID,
                    "stored stage manifest predecessor chain is invalid",
                    task_name=task_name,
                    safe_details=(("stage", stage.value),),
                )
            manifests.append(manifest)
            predecessor_sha256 = manifest.manifest_sha256
        return tuple(manifests)
