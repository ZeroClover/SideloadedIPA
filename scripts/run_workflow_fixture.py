#!/usr/bin/env python3
"""Exercise the file-backed nine-stage workflow contract without external effects."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.domain import PipelineStage, StageManifest
from sideloadedipa.manifest_store import FileStageManifestStore
from sideloadedipa.pipeline_application import ManifestPipelineUseCases, StageOutput


@dataclass
class FixtureClock:
    value: datetime = datetime(2026, 7, 21, tzinfo=timezone.utc)

    def now(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    store = FileStageManifestStore(args.output / "manifests")

    def operation(
        request: CommandRequest,
        task_name: str,
        predecessor: StageManifest | None,
    ) -> StageOutput:
        del request
        stage = (
            PipelineStage.SOURCE
            if predecessor is None
            else PipelineStage(
                list(PipelineStage)[list(PipelineStage).index(predecessor.stage) + 1]
            )
        )
        content = f"{task_name}:{stage.value}:{predecessor.manifest_sha256 if predecessor else ''}"
        return StageOutput(hashlib.sha256(content.encode()).hexdigest())

    operations = {stage: operation for stage in PipelineStage}
    pipeline = ManifestPipelineUseCases(
        operations=operations,
        manifests=store,
        clock=FixtureClock(),
        select_tasks=lambda request: request.task_names,
    )
    request = CommandRequest(
        CommandName.RUN,
        Path("configs/tasks.toml"),
        ("Workflow Fixture",),
        OutputFormat.JSON,
        apply=True,
        publish=True,
    )
    result = pipeline.run(request)
    manifests = [store.load("Workflow Fixture", stage) for stage in PipelineStage]
    if result.exit_code != 0 or any(value is None for value in manifests):
        return 1
    document = {
        "schema_version": 1,
        "task_name": "Workflow Fixture",
        "stages": [
            {
                "stage": value.stage.value,
                "status": value.status.value,
                "manifest_sha256": value.manifest_sha256,
                "predecessor_sha256": value.predecessor_sha256,
            }
            for value in manifests
            if value is not None
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "workflow-fixture-summary.json").write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
