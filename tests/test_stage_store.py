"""Tests for filesystem-backed production stage manifests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sideloadedipa.domain import PipelineStage, StageStatus
from sideloadedipa.errors import ConfigurationError
from sideloadedipa.manifest_store import FileStageManifestStore
from sideloadedipa.stage_manifests import finish_stage, start_stage

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def succeeded(task_name: str, stage: PipelineStage, predecessor=None):  # type: ignore[no-untyped-def]
    running = start_stage(
        task_name=task_name,
        stage=stage,
        started_at=NOW,
        input_sha256=predecessor.result_sha256 if predecessor else None,
        predecessor=predecessor,
    )
    return finish_stage(
        running,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW,
        result_sha256=stage.value.encode().hex().ljust(64, "0")[:64],
    )


def test_store_isolates_run_and_task_and_loads_valid_chain(tmp_path: Path) -> None:
    store = FileStageManifestStore(tmp_path, "run/123")
    source = succeeded("Task / A", PipelineStage.SOURCE)
    inventory = succeeded("Task / A", PipelineStage.INVENTORY, source)

    store.save(source)
    source_path = store.path("Task / A", PipelineStage.SOURCE)
    store.save(inventory)

    assert source_path.is_relative_to(tmp_path)
    assert store.load("Task / A", PipelineStage.SOURCE) == source
    assert store.completed("Task / A") == (source, inventory)
    assert FileStageManifestStore(tmp_path, "other").completed("Task / A") == ()


def test_store_rejects_tampered_manifest(tmp_path: Path) -> None:
    store = FileStageManifestStore(tmp_path, "run")
    source = succeeded("Task", PipelineStage.SOURCE)
    store.save(source)
    path = store.path("Task", PipelineStage.SOURCE)
    document = json.loads(path.read_text())
    document["result_sha256"] = "f" * 64
    path.write_text(json.dumps(document))

    with pytest.raises(ConfigurationError, match="digest is invalid"):
        store.load("Task", PipelineStage.SOURCE)


def test_store_rejects_broken_predecessor_chain(tmp_path: Path) -> None:
    store = FileStageManifestStore(tmp_path, "run")
    source = succeeded("Task", PipelineStage.SOURCE)
    inventory = succeeded("Task", PipelineStage.INVENTORY, source)
    store.save(inventory)

    with pytest.raises(ConfigurationError, match="predecessor chain"):
        store.completed("Task")
