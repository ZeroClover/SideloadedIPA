"""Tests for typed ordered pipeline-stage manifests."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from sideloadedipa.domain import (
    Diagnostic,
    DiagnosticSeverity,
    PipelineStage,
    StageManifest,
    StageStatus,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.stage_manifests import (
    PIPELINE_STAGE_ORDER,
    STAGE_MANIFEST_SCHEMA_VERSION,
    canonical_stage_manifest_json,
    finish_stage,
    skip_stage,
    stage_manifest_sha256,
    start_stage,
)

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def _completed_source() -> StageManifest:
    started = start_stage(
        task_name="Example",
        stage=PipelineStage.SOURCE,
        started_at=NOW,
        input_sha256="a" * 64,
    )
    return finish_stage(
        started,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=1),
        result_sha256="b" * 64,
    )


def test_stage_order_matches_the_required_pipeline() -> None:
    assert [stage.value for stage in PIPELINE_STAGE_ORDER] == [
        "source",
        "inventory",
        "policy",
        "resource-plan",
        "resource-apply",
        "signing-plan",
        "sign",
        "verify",
        "publish",
    ]


def test_starts_and_finishes_linked_canonical_manifests() -> None:
    source = _completed_source()
    inventory = start_stage(
        task_name="Example",
        stage=PipelineStage.INVENTORY,
        started_at=NOW + timedelta(seconds=2),
        input_sha256=source.result_sha256,
        predecessor=source,
    )
    inventory = finish_stage(
        inventory,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=3),
        result_sha256="c" * 64,
    )

    document = json.loads(canonical_stage_manifest_json(inventory))
    assert document["schema_version"] == STAGE_MANIFEST_SCHEMA_VERSION
    assert document["predecessor_sha256"] == source.manifest_sha256
    assert document["manifest_sha256"] == inventory.manifest_sha256
    assert inventory.manifest_sha256 == stage_manifest_sha256(inventory)


@pytest.mark.parametrize(
    "stage",
    [PipelineStage.INVENTORY, PipelineStage.SIGN, PipelineStage.PUBLISH],
)
def test_pipeline_cannot_start_after_source(stage: PipelineStage) -> None:
    with pytest.raises(DomainError) as caught:
        start_stage(
            task_name="Example",
            stage=stage,
            started_at=NOW,
            input_sha256="a" * 64,
        )

    assert caught.value.code is ErrorCode.PIPELINE_TRANSITION_INVALID


def test_rejects_out_of_order_failed_or_tampered_predecessor() -> None:
    source = _completed_source()
    failed = finish_stage(
        start_stage(
            task_name="Example",
            stage=PipelineStage.SOURCE,
            started_at=NOW,
            input_sha256="a" * 64,
        ),
        status=StageStatus.FAILED,
        completed_at=NOW + timedelta(seconds=1),
    )
    tampered = replace(source, result_sha256="0" * 64)

    for predecessor, match in (
        (source, "out of order"),
        (failed, "did not succeed"),
        (tampered, "digest is invalid"),
    ):
        with pytest.raises(DomainError, match=match):
            start_stage(
                task_name="Example",
                stage=(PipelineStage.POLICY if predecessor is source else PipelineStage.INVENTORY),
                started_at=NOW,
                input_sha256="c" * 64,
                predecessor=predecessor,
            )

    with pytest.raises(DomainError, match="another task"):
        start_stage(
            task_name="Other",
            stage=PipelineStage.INVENTORY,
            started_at=NOW,
            input_sha256="c" * 64,
            predecessor=source,
        )


def test_failed_stage_forces_ordered_skips() -> None:
    source = _completed_source()
    inventory = start_stage(
        task_name="Example",
        stage=PipelineStage.INVENTORY,
        started_at=NOW,
        input_sha256=source.result_sha256,
        predecessor=source,
    )
    failure = Diagnostic(
        "inventory.invalid",
        DiagnosticSeverity.ERROR,
        "inventory failed",
        task_name="Example",
    )
    inventory = finish_stage(
        inventory,
        status=StageStatus.FAILED,
        completed_at=NOW + timedelta(seconds=1),
        diagnostics=(failure,),
    )
    policy = skip_stage(
        task_name="Example",
        stage=PipelineStage.POLICY,
        skipped_at=NOW + timedelta(seconds=2),
        predecessor=inventory,
        diagnostics=(failure,),
    )
    resource_plan = skip_stage(
        task_name="Example",
        stage=PipelineStage.RESOURCE_PLAN,
        skipped_at=NOW + timedelta(seconds=3),
        predecessor=policy,
        diagnostics=(failure,),
    )

    assert policy.status is StageStatus.SKIPPED
    assert resource_plan.predecessor_sha256 == policy.manifest_sha256
    with pytest.raises(DomainError, match="only after"):
        skip_stage(
            task_name="Example",
            stage=PipelineStage.INVENTORY,
            skipped_at=NOW,
            predecessor=source,
        )


def test_stage_cannot_finish_twice_or_succeed_without_a_result() -> None:
    source = _completed_source()

    with pytest.raises(DomainError, match="only a running"):
        finish_stage(source, status=StageStatus.FAILED, completed_at=NOW)
    running = start_stage(
        task_name="Example",
        stage=PipelineStage.INVENTORY,
        started_at=NOW,
        input_sha256=source.result_sha256,
        predecessor=source,
    )
    with pytest.raises(DomainError, match="requires a result"):
        finish_stage(running, status=StageStatus.SUCCEEDED, completed_at=NOW)
    with pytest.raises(DomainError, match="succeeded or failed"):
        finish_stage(running, status=StageStatus.SKIPPED, completed_at=NOW)


def test_skip_rejects_out_of_order_and_tampered_predecessors() -> None:
    failed = finish_stage(
        start_stage(
            task_name="Example",
            stage=PipelineStage.SOURCE,
            started_at=NOW,
            input_sha256="a" * 64,
        ),
        status=StageStatus.FAILED,
        completed_at=NOW,
    )

    with pytest.raises(DomainError, match="out of order"):
        skip_stage(
            task_name="Example",
            stage=PipelineStage.POLICY,
            skipped_at=NOW,
            predecessor=failed,
        )
    with pytest.raises(DomainError, match="digest is invalid"):
        skip_stage(
            task_name="Example",
            stage=PipelineStage.INVENTORY,
            skipped_at=NOW,
            predecessor=replace(failed, result_sha256="0" * 64),
        )


def test_canonical_serialization_rejects_content_tampering() -> None:
    source = _completed_source()

    with pytest.raises(ValueError, match="digest"):
        canonical_stage_manifest_json(replace(source, result_sha256="0" * 64))
