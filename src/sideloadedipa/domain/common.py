"""Small value types shared by domain modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class FrozenJsonObject:
    items: tuple[tuple[str, "FrozenJsonValue"], ...]


FrozenJsonValue: TypeAlias = (
    str | int | float | bool | None | tuple["FrozenJsonValue", ...] | FrozenJsonObject
)


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: DiagnosticSeverity
    message: str
    task_name: str | None = None
    bundle_id: str | None = None
    remediation: str | None = None
    details: tuple[tuple[str, FrozenJsonValue], ...] = ()
