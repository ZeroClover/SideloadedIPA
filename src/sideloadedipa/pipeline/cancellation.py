"""Redacted durable evidence for interrupted pipeline runs."""

from __future__ import annotations

import json
import signal
from asyncio import CancelledError
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.util.atomics import atomic_write_bytes, canonical_json


@dataclass(slots=True)
class SideEffectJournal:
    created_apple_resources: list[tuple[str, str]] = field(default_factory=list)
    publication_committed: bool = False

    def record_apple_resource(self, resource_kind: str, resource_id: str) -> None:
        self.created_apple_resources.append((resource_kind, resource_id))

    def mark_publication_committed(self) -> None:
        self.publication_committed = True

    def document(self, *, cancelled: bool = True) -> dict[str, object]:
        return {
            "schema_version": 1,
            "cancelled": cancelled,
            "created_apple_resources": [
                {"kind": kind, "resource_id": resource_id}
                for kind, resource_id in self.created_apple_resources
            ],
            "publication_committed": self.publication_committed,
        }


def _write_atomic(path: Path, document: dict[str, object]) -> None:
    atomic_write_bytes(path, canonical_json(document) + b"\n")


def load_side_effect_journal(path: Path) -> SideEffectJournal:
    if not path.exists():
        return SideEffectJournal()
    try:
        document = json.loads(path.read_text())
        resources = document["created_apple_resources"]
        committed = document["publication_committed"]
        if (
            document.get("schema_version") != 1
            or not isinstance(resources, list)
            or not isinstance(committed, bool)
        ):
            raise TypeError
        created = [
            (value["kind"], value["resource_id"])
            for value in resources
            if isinstance(value, dict)
            and isinstance(value.get("kind"), str)
            and isinstance(value.get("resource_id"), str)
        ]
        if len(created) != len(resources):
            raise TypeError
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "side-effect journal is invalid",
            remediation="restart the pipeline with a new run ID",
        ) from error
    return SideEffectJournal(created, committed)


def write_side_effect_journal(path: Path, journal: SideEffectJournal) -> None:
    _write_atomic(path, journal.document(cancelled=False))


@contextmanager
def record_cancellation(journal: SideEffectJournal, report_path: Path) -> Iterator[None]:
    try:
        yield
    except (KeyboardInterrupt, CancelledError):
        _write_atomic(report_path, journal.document())
        raise


@contextmanager
def route_sigterm_to_cancellation() -> Iterator[None]:
    """Turn CI's termination signal into the existing cancellation path."""

    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
