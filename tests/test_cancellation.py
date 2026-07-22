"""Tests for cancellation cleanup and durable side-effect evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sideloadedipa.errors import ConfigurationError
from sideloadedipa.pipeline.cancellation import (
    SideEffectJournal,
    load_side_effect_journal,
    record_cancellation,
    write_side_effect_journal,
)
from sideloadedipa.util.workspace import task_workspace


def test_cancellation_removes_workspace_and_records_created_resources(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "cancelled.json"
    journal = SideEffectJournal()
    workspace_root: Path | None = None

    with pytest.raises(KeyboardInterrupt):
        with task_workspace(tmp_path / "work", "Example") as workspace:
            workspace_root = workspace.root
            workspace.source_ipa.write_bytes(b"private source")
            with record_cancellation(journal, report):
                journal.record_apple_resource("bundle-id", "RESOURCE-1")
                raise KeyboardInterrupt

    assert workspace_root is not None and not workspace_root.exists()
    document = json.loads(report.read_text())
    assert document == {
        "schema_version": 1,
        "cancelled": True,
        "created_apple_resources": [{"kind": "bundle-id", "resource_id": "RESOURCE-1"}],
        "publication_committed": False,
    }


def test_completed_scope_writes_no_cancellation_report(tmp_path: Path) -> None:
    report = tmp_path / "cancelled.json"
    journal = SideEffectJournal()

    with record_cancellation(journal, report):
        journal.mark_publication_committed()

    assert not report.exists()


def test_side_effect_journal_round_trips_between_stage_processes(tmp_path: Path) -> None:
    path = tmp_path / "side-effects.json"
    journal = SideEffectJournal([("bundle-id", "BUNDLE_NEW")], True)

    write_side_effect_journal(path, journal)

    assert load_side_effect_journal(path) == journal
    document = json.loads(path.read_text())
    assert document["cancelled"] is False

    path.write_text("{}")
    with pytest.raises(ConfigurationError, match="journal is invalid"):
        load_side_effect_journal(path)

    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "created_apple_resources": [],
                "publication_committed": False,
            }
        )
    )
    with pytest.raises(ConfigurationError, match="journal is invalid"):
        load_side_effect_journal(path)

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_apple_resources": [{}],
                "publication_committed": False,
            }
        )
    )
    with pytest.raises(ConfigurationError, match="journal is invalid"):
        load_side_effect_journal(path)
