"""Tests for complete redacted pipeline run reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.cache_decisions import RebuildDecision, RebuildReason
from sideloadedipa.domain import PipelineStage, PublicationResult, SourceAsset, StageStatus
from sideloadedipa.run_reports import (
    RunReport,
    TaskRunEvidence,
    canonical_run_report_json,
    human_run_report,
    write_run_report,
)
from sideloadedipa.stage_manifests import finish_stage, start_stage
from tests.test_publication import candidate

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def evidence(tmp_path: Path, secret: str = "") -> TaskRunEvidence:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    publication_candidate = candidate(artifact)
    running = start_stage(
        task_name="Example",
        stage=PipelineStage.SOURCE,
        started_at=NOW,
        input_sha256=None,
    )
    stage = finish_stage(
        running,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=2),
        result_sha256="5" * 64,
    )
    return TaskRunEvidence(
        task_name="Example",
        stages=(stage,),
        source=SourceAsset(
            "asset-1",
            f"Example-{secret}.ipa",
            f"https://example.invalid/download?token={secret}",
            "1.2.3",
            NOW - timedelta(days=1),
            PurePosixPath("private/Example.ipa"),
            "0" * 64,
        ),
        graph_sha256=publication_candidate.plan.graph_sha256,
        plan=publication_candidate.plan,
        capability_classifications=(("io.example.app", "APP_GROUPS", "api-additive"),),
        manual_actions=(f"review {secret} at /private/key.p8",),
        apple_resource_ids=(("bundle-id", "RESOURCE-1"),),
        cache_decision=RebuildDecision(
            "Example", True, RebuildReason.FINGERPRINT_CHANGED, "6" * 64, "7" * 64
        ),
        verification=publication_candidate.verification,
        publication=PublicationResult(
            "Example",
            "apps/example/1.2.3/hash-Example.ipa",
            "https://cdn.example/apps/example/1.2.3/hash-Example.ipa",
            publication_candidate.artifact_sha256,
            "site/apps.json",
            "8" * 64,
            ("apps/example/1.0/Example.ipa",),
        ),
    )


def test_report_contains_complete_provenance_and_redacts_secrets(tmp_path: Path) -> None:
    secret = "PRIVATE-TOKEN"
    private_path = Path("/private/key.p8")
    report = RunReport("run-123", NOW, NOW + timedelta(seconds=3), (evidence(tmp_path, secret),))

    payload = canonical_run_report_json(
        report,
        secret_redactions=(secret,),
        path_redactions=(private_path,),
    )
    document = json.loads(payload)
    task = document["tasks"][0]

    assert secret not in payload.decode()
    assert str(private_path) not in payload.decode()
    assert "download?token" not in payload.decode()
    assert document["schema_version"] == 1
    assert document["passed"] is True
    assert task["stages"][0]["duration_seconds"] == 2
    assert task["source"]["version"] == "1.2.3"
    assert task["signing_plan"]["certificate_sha256"] == "2" * 64
    assert task["capability_classifications"][0]["classification"] == "api-additive"
    assert task["manual_actions"] == ["review *** at ***"]
    assert task["apple_resource_ids"][0]["resource_id"] == "RESOURCE-1"
    assert task["cache"]["reason"] == "fingerprint-changed"
    assert task["verification"]["passed"] is True
    assert task["publication"]["registry_key"] == "site/apps.json"

    digest = document.pop("report_sha256")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    assert digest == hashlib.sha256(canonical).hexdigest()


def test_writes_atomic_json_and_concise_human_summary(tmp_path: Path) -> None:
    report = RunReport("run-123", NOW, NOW + timedelta(seconds=3), (evidence(tmp_path),))
    output = tmp_path / "reports" / "run.json"

    write_run_report(output, report)

    assert json.loads(output.read_text())["run_id"] == "run-123"
    assert human_run_report(report) == (
        "Run run-123: 1 task(s), 3.00s\n" "Example: succeeded; cache=fingerprint-changed; published"
    )


@pytest.mark.parametrize("mismatch", ["stage", "plan", "verification", "publication"])
def test_rejects_cross_task_or_digest_mismatches(tmp_path: Path, mismatch: str) -> None:
    task = evidence(tmp_path)
    if mismatch == "stage":
        task = replace(task, stages=(replace(task.stages[0], task_name="Other"),))
    elif mismatch == "plan":
        assert task.plan is not None
        task = replace(task, plan=replace(task.plan, task_name="Other"))
    elif mismatch == "verification":
        assert task.verification is not None
        task = replace(task, verification=replace(task.verification, plan_sha256="9" * 64))
    else:
        assert task.publication is not None
        task = replace(task, publication=replace(task.publication, artifact_sha256="9" * 64))

    with pytest.raises(ValueError):
        canonical_run_report_json(RunReport("run-123", NOW, NOW + timedelta(seconds=3), (task,)))
