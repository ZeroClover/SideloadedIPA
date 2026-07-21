"""Atomic filesystem storage for redacted pipeline-stage manifests."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from sideloadedipa.domain import PipelineStage, StageManifest
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.stage_manifests import (
    canonical_stage_manifest_json,
    parse_stage_manifest_json,
)


class FileStageManifestStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, task_name: str, stage: PipelineStage) -> Path:
        task_key = hashlib.sha256(task_name.encode()).hexdigest()[:16]
        return self.root / task_key / f"{stage.value}.json"

    def load(self, task_name: str, stage: PipelineStage) -> StageManifest | None:
        path = self._path(task_name, stage)
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
        path = self._path(manifest.task_name, manifest.stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_stage_manifest_json(manifest)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(temporary).unlink(missing_ok=True)
            raise
