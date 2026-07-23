"""Canonical run/task-bound source and unsigned inventory manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    PipelineStage,
    SourceAsset,
    StageStatus,
)
from sideloadedipa.errors import ConfigurationError
from sideloadedipa.pipeline.input_manifests import CanonicalInputManifestStore
from sideloadedipa.pipeline.inspection import ResolvedSource
from sideloadedipa.pipeline.manifest_store import FileStageManifestStore
from sideloadedipa.pipeline.sign_stage import json_digest
from sideloadedipa.pipeline.stage_manifests import finish_stage, start_stage
from sideloadedipa.sources import DownloadedSource
from sideloadedipa.util import atomics
from sideloadedipa.util.atomics import canonical_json

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def graph_for(source_sha256: str) -> BundleGraph:
    document = {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "root_path": "Payload/App.app",
        "nodes": [],
    }
    return BundleGraph(
        PurePosixPath("Payload/App.app"),
        (),
        source_sha256,
        hashlib.sha256(canonical_json(document)).hexdigest(),
    )


def rewrite(path: Path, **mutations: object) -> None:
    document = json.loads(path.read_text())
    document.update(mutations)
    document.pop("manifest_sha256")
    document["manifest_sha256"] = hashlib.sha256(canonical_json(document)).hexdigest()
    path.write_bytes(canonical_json(document))


def resign(document: dict[str, object]) -> bytes:
    document.pop("manifest_sha256", None)
    document["manifest_sha256"] = hashlib.sha256(canonical_json(document)).hexdigest()
    return canonical_json(document)


def manifest_fixture(tmp_path: Path, *, run_id: str = "run-one"):
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    stages = FileStageManifestStore(tmp_path / "pipeline", run_id)
    inputs = CanonicalInputManifestStore(stages)
    path = inputs.source_path(task.task_name)
    path.parent.mkdir(parents=True)
    content = b"canonical unsigned source"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    downloaded = DownloadedSource(path, len(content), digest, attempts=2)
    resolved = ResolvedSource(
        "https://downloads.example/App.ipa",
        f"sha256:{digest}",
        {
            "kind": task.source.kind.value,
            "asset_id": "42",
            "asset_name": "App.ipa",
            "release_tag": "v1",
            "expected_sha256": digest,
            "actual_sha256": digest,
            "actual_size": len(content),
            "download_attempts": 2,
        },
        len(content),
    )
    source = SourceAsset(
        "42",
        "App.ipa",
        resolved.url,
        "v1",
        NOW,
        PurePosixPath(path.name),
        digest,
    )
    source_stage = finish_stage(
        start_stage(
            task_name=task.task_name,
            stage=PipelineStage.SOURCE,
            started_at=NOW,
            input_sha256=None,
        ),
        status=StageStatus.SUCCEEDED,
        completed_at=NOW,
        result_sha256=json_digest(asdict(source)),
    )
    stages.save(source_stage)
    source_manifest = inputs.save_source(
        task=task,
        resolved=resolved,
        downloaded=downloaded,
        source=source,
        source_stage=source_stage,
    )
    graph = graph_for(digest)
    inventory_stage = finish_stage(
        start_stage(
            task_name=task.task_name,
            stage=PipelineStage.INVENTORY,
            started_at=NOW,
            input_sha256=source_stage.result_sha256,
            predecessor=source_stage,
        ),
        status=StageStatus.SUCCEEDED,
        completed_at=NOW,
        result_sha256=graph.graph_sha256,
    )
    stages.save(inventory_stage)
    inventory_manifest = inputs.save_inventory(
        task=task,
        source_manifest=source_manifest,
        graph=graph,
        inventory_stage=inventory_stage,
    )
    return task, stages, inputs, source_manifest, inventory_manifest


def test_round_trips_run_bound_source_and_inventory_evidence(tmp_path: Path) -> None:
    task, _stages, inputs, source_manifest, inventory_manifest = manifest_fixture(tmp_path)

    loaded = inputs.load(task)

    assert loaded.source_manifest == source_manifest
    assert loaded.inventory_manifest == inventory_manifest
    assert loaded.downloaded.path == inputs.source_path(task.task_name)
    assert loaded.downloaded.sha256 == loaded.graph.source_sha256
    assert loaded.resolved.expected_sha256 == f"sha256:{loaded.downloaded.sha256}"
    assert inputs.source_manifest_path(task.task_name).stat().st_mode & 0o777 == 0o600
    assert inputs.inventory_manifest_path(task.task_name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("run_id", "other-run"),
        ("task_name", "Other Task"),
        ("schema_version", 2),
        ("actual_sha256", "0" * 64),
    ],
)
def test_rejects_cross_identity_schema_and_digest_mismatches(
    tmp_path: Path,
    mutation: str,
    value: object,
) -> None:
    task, _stages, inputs, _source, _inventory = manifest_fixture(tmp_path)
    rewrite(inputs.source_manifest_path(task.task_name), **{mutation: value})

    with pytest.raises(ConfigurationError):
        inputs.load(task)


@pytest.mark.parametrize(
    "malformation",
    [
        "root-array",
        "missing-digest",
        "invalid-document-digest",
        "invalid-sha256",
        "empty-run-id",
        "boolean-size",
        "non-object-evidence",
        "source-fields",
        "source-path-traversal",
        "invalid-timestamp",
        "naive-timestamp",
    ],
)
def test_rejects_malformed_source_manifest_fields(
    tmp_path: Path,
    malformation: str,
) -> None:
    task, _stages, inputs, _source, _inventory = manifest_fixture(tmp_path)
    path = inputs.source_manifest_path(task.task_name)
    document = json.loads(path.read_bytes())
    if malformation == "root-array":
        payload = canonical_json([])
    elif malformation == "missing-digest":
        document.pop("manifest_sha256")
        payload = canonical_json(document)
    elif malformation == "invalid-document-digest":
        document["manifest_sha256"] = "0" * 64
        payload = canonical_json(document)
    else:
        source = document["source"]
        assert isinstance(source, dict)
        if malformation == "invalid-sha256":
            document["actual_sha256"] = "not-a-digest"
        elif malformation == "empty-run-id":
            document["run_id"] = ""
        elif malformation == "boolean-size":
            document["expected_size"] = True
        elif malformation == "non-object-evidence":
            document["evidence"] = []
        elif malformation == "source-fields":
            source.pop("name")
        elif malformation == "source-path-traversal":
            source["path"] = "../source.ipa"
        elif malformation == "invalid-timestamp":
            source["published_at"] = "not-a-timestamp"
        else:
            source["published_at"] = "2026-07-23T00:00:00"
        payload = resign(document)
    path.write_bytes(payload)

    with pytest.raises(ConfigurationError):
        inputs.load(task)


@pytest.mark.parametrize("malformation", ["schema", "nodes-type", "noncanonical-graph"])
def test_rejects_malformed_inventory_manifest_fields(
    tmp_path: Path,
    malformation: str,
) -> None:
    task, _stages, inputs, _source, _inventory = manifest_fixture(tmp_path)
    path = inputs.inventory_manifest_path(task.task_name)
    document = json.loads(path.read_bytes())
    graph = document["graph"]
    assert isinstance(graph, dict)
    if malformation == "schema":
        document["schema_version"] = 2
    elif malformation == "nodes-type":
        graph["nodes"] = {}
    else:
        graph["unexpected"] = True
    path.write_bytes(resign(document))

    with pytest.raises(ConfigurationError):
        inputs.load(task)


@pytest.mark.parametrize("target", ["source", "inventory"])
def test_rejects_missing_or_truncated_input_manifest(tmp_path: Path, target: str) -> None:
    task, _stages, inputs, _source, _inventory = manifest_fixture(tmp_path)
    path = (
        inputs.source_manifest_path(task.task_name)
        if target == "source"
        else inputs.inventory_manifest_path(task.task_name)
    )
    path.unlink()
    with pytest.raises(ConfigurationError):
        inputs.load(task)

    path.write_bytes(b'{"truncated":')
    with pytest.raises(ConfigurationError):
        inputs.load(task)


def test_rejects_tampered_source_file_and_graph_digest(tmp_path: Path) -> None:
    task, _stages, inputs, _source, _inventory = manifest_fixture(tmp_path)
    source_path = inputs.source_path(task.task_name)
    source_path.write_bytes(b"tampered")
    with pytest.raises(ConfigurationError):
        inputs.load(task)

    task, _stages, inputs, _source, _inventory = manifest_fixture(
        tmp_path / "graph", run_id="graph-run"
    )
    path = inputs.inventory_manifest_path(task.task_name)
    document = json.loads(path.read_text())
    document["graph"]["graph_sha256"] = "0" * 64
    document.pop("manifest_sha256")
    document["manifest_sha256"] = hashlib.sha256(canonical_json(document)).hexdigest()
    path.write_bytes(canonical_json(document))
    with pytest.raises(ConfigurationError):
        inputs.load(task)


def test_rejects_failed_predecessor_stage(tmp_path: Path) -> None:
    task, stages, inputs, _source, _inventory = manifest_fixture(tmp_path)
    source_stage = stages.load(task.task_name, PipelineStage.SOURCE)
    assert source_stage is not None
    failed = finish_stage(
        start_stage(
            task_name=task.task_name,
            stage=PipelineStage.INVENTORY,
            started_at=NOW,
            input_sha256=source_stage.result_sha256,
            predecessor=source_stage,
        ),
        status=StageStatus.FAILED,
        completed_at=NOW,
    )
    stages.save(failed)

    with pytest.raises(ConfigurationError):
        inputs.load(task)


def test_atomic_manifest_write_does_not_expose_partial_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    stages = FileStageManifestStore(tmp_path / "pipeline", "interrupted")
    inputs = CanonicalInputManifestStore(stages)
    path = inputs.source_path(task.task_name)
    path.parent.mkdir(parents=True)
    content = b"source"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    downloaded = DownloadedSource(path, len(content), digest)
    resolved = ResolvedSource(
        "https://downloads.example/App.ipa",
        f"sha256:{digest}",
        {"kind": task.source.kind.value, "expected_sha256": digest},
        len(content),
    )
    source = SourceAsset(
        "asset",
        "App.ipa",
        resolved.url,
        "v1",
        NOW,
        PurePosixPath(path.name),
        digest,
    )
    source_stage = finish_stage(
        start_stage(
            task_name=task.task_name,
            stage=PipelineStage.SOURCE,
            started_at=NOW,
            input_sha256=None,
        ),
        status=StageStatus.SUCCEEDED,
        completed_at=NOW,
        result_sha256=json_digest(asdict(source)),
    )

    def fail_replace(source_path: Path, destination: Path) -> None:
        del source_path, destination
        raise OSError("fixture interruption")

    monkeypatch.setattr(atomics.os, "replace", fail_replace)
    with pytest.raises(OSError):
        inputs.save_source(
            task=task,
            resolved=resolved,
            downloaded=downloaded,
            source=source,
            source_stage=source_stage,
        )

    assert not inputs.source_manifest_path(task.task_name).exists()
    assert not list(inputs.source_manifest_path(task.task_name).parent.glob("*.tmp"))
