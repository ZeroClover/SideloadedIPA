"""Tests for isolated task workspace lifecycle."""

from __future__ import annotations

from pathlib import Path

from sideloadedipa.util.workspace import task_workspace


def test_workspace_paths_are_frozen_unique_and_cleaned(tmp_path: Path) -> None:
    base = tmp_path / "work"

    with task_workspace(base, "LiveContainer / standard") as first:
        first_root = first.root
        assert first.root.name.startswith("LiveContainer-standard-")
        assert first.source_ipa.parent == first.root
        assert first.extracted.is_dir()
        assert first.reports.is_dir()
        assert not first.output_ipa.exists()

    assert not first_root.exists()

    with task_workspace(base, "LiveContainer / standard") as second:
        assert second.root != first_root

    assert list(base.iterdir()) == []
