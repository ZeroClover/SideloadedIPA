"""Tests for side-effect Protocol boundaries."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from sideloadedipa.domain import SourceAsset, SourceConfig, SourceKind, Task
from sideloadedipa.ports import Clock, Filesystem, SourceRepository


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 21, tzinfo=UTC)


class LocalFilesystem:
    def temporary_directory(self, prefix: str) -> AbstractContextManager[Path]:
        return nullcontext(Path(prefix))

    def copy_file(self, source: Path, destination: Path) -> None:
        return None

    def atomic_replace(self, source: Path, destination: Path) -> None:
        return None

    def remove_tree(self, path: Path) -> None:
        return None


class StaticSourceRepository:
    def fetch(self, task: Task, destination: Path) -> SourceAsset:
        return SourceAsset(
            asset_id="asset-1",
            name="App.ipa",
            source_url=task.source.location,
            version="1.0",
            published_at=None,
            path=destination,
            sha256="a" * 64,
        )


def test_structural_protocols_accept_small_adapters(tmp_path: Path) -> None:
    task = Task(
        task_name="App",
        app_name="App",
        bundle_id="com.example.app",
        source=SourceConfig(SourceKind.DIRECT_URL, "https://example.com/App.ipa"),
        slug="app",
    )
    source = StaticSourceRepository()

    assert isinstance(FixedClock(), Clock)
    assert isinstance(LocalFilesystem(), Filesystem)
    assert isinstance(source, SourceRepository)
    assert source.fetch(task, tmp_path / "App.ipa").asset_id == "asset-1"
