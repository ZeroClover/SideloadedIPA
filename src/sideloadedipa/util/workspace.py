"""Immutable task-scoped workspace paths and lifecycle."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TaskWorkspace:
    root: Path
    source_ipa: Path
    extracted: Path
    output_ipa: Path
    reports: Path


def _safe_prefix(task_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", task_name).strip(".-")
    return f"{value or 'task'}-"


@contextmanager
def task_workspace(base_directory: Path, task_name: str) -> Iterator[TaskWorkspace]:
    """Yield one unique workspace and remove it when the task completes."""

    base_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=_safe_prefix(task_name), dir=base_directory
    ) as temporary:
        root = Path(temporary)
        extracted = root / "extracted"
        reports = root / "reports"
        extracted.mkdir()
        reports.mkdir()
        yield TaskWorkspace(
            root=root,
            source_ipa=root / "source.ipa",
            extracted=extracted,
            output_ipa=root / "signed.ipa",
            reports=reports,
        )
