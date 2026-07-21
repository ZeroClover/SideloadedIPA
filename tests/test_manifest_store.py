"""Tests for file-backed stage manifest handoff."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sideloadedipa.domain import PipelineStage, StageStatus
from sideloadedipa.errors import ConfigurationError
from sideloadedipa.manifest_store import FileStageManifestStore
from sideloadedipa.stage_manifests import (
    canonical_stage_manifest_json,
    finish_stage,
    parse_stage_manifest_json,
    start_stage,
)


def succeeded_source(task_name: str = "Example"):
    running = start_stage(
        task_name=task_name,
        stage=PipelineStage.SOURCE,
        started_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        input_sha256=None,
    )
    return finish_stage(
        running,
        status=StageStatus.SUCCEEDED,
        completed_at=datetime(2026, 7, 21, 0, 0, 1, tzinfo=timezone.utc),
        result_sha256="a" * 64,
    )


def test_round_trips_canonical_manifest_with_private_mode(tmp_path: Path) -> None:
    store = FileStageManifestStore(tmp_path)
    manifest = succeeded_source()

    store.save(manifest)

    assert store.load("Example", PipelineStage.SOURCE) == manifest
    path = next(tmp_path.rglob("source.json"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["manifest_sha256"] == manifest.manifest_sha256
    assert store.load("Missing", PipelineStage.SOURCE) is None


def test_rejects_tampered_or_misaddressed_manifest(tmp_path: Path) -> None:
    store = FileStageManifestStore(tmp_path)
    store.save(succeeded_source("../Example"))
    path = next(tmp_path.rglob("source.json"))
    document = json.loads(path.read_text())
    document["result_sha256"] = "0" * 64
    path.write_text(json.dumps(document))

    with pytest.raises(ConfigurationError):
        store.load("../Example", PipelineStage.SOURCE)
    assert path.is_relative_to(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"diagnostics":{}}',
        b'{"diagnostics":[{"details":[]}]}',
    ],
)
def test_rejects_malformed_manifest_documents(payload: bytes) -> None:
    with pytest.raises(ConfigurationError):
        parse_stage_manifest_json(payload)


@pytest.mark.parametrize("mutation", ["schema", "timezone", "digest"])
def test_rejects_unsupported_or_untrusted_manifest_fields(mutation: str) -> None:
    document = json.loads(canonical_stage_manifest_json(succeeded_source()))
    if mutation == "schema":
        document["schema_version"] = 2
    elif mutation == "timezone":
        document["started_at"] = "2026-07-21T00:00:00"
    else:
        document["manifest_sha256"] = "0" * 64

    with pytest.raises(ConfigurationError):
        parse_stage_manifest_json(json.dumps(document).encode())


def test_rejects_manifest_stored_under_another_task_identity(tmp_path: Path) -> None:
    store = FileStageManifestStore(tmp_path)
    manifest = succeeded_source("Example")
    task_key = hashlib.sha256(b"Other").hexdigest()[:16]
    path = tmp_path / task_key / "source.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_stage_manifest_json(manifest))

    with pytest.raises(ConfigurationError):
        store.load("Other", PipelineStage.SOURCE)


def test_atomic_save_removes_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileStageManifestStore(tmp_path)

    def fail_replace(source: Path, destination: Path) -> Path:
        del source, destination
        raise OSError("fixture replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        store.save(succeeded_source())

    assert list(tmp_path.rglob("*.json")) == []


def test_workflow_fixture_reopens_complete_predecessor_chain(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "scripts/run_workflow_fixture.py", "--output", str(tmp_path)],
        check=True,
    )

    summary = json.loads((tmp_path / "workflow-fixture-summary.json").read_text())
    stages = summary["stages"]
    assert [value["stage"] for value in stages] == [value.value for value in PipelineStage]
    assert all(value["status"] == "succeeded" for value in stages)
    assert stages[0]["predecessor_sha256"] is None
    assert all(
        current["predecessor_sha256"] == previous["manifest_sha256"]
        for previous, current in zip(stages, stages[1:])
    )
