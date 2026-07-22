"""Small value types shared by domain modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class FrozenJsonObject:
    items: tuple[tuple[str, "FrozenJsonValue"], ...]


FrozenJsonValue: TypeAlias = (
    str | int | float | bool | None | tuple["FrozenJsonValue", ...] | FrozenJsonObject
)


def freeze_json(value: object) -> FrozenJsonValue:
    """Convert decoded JSON-compatible data into immutable domain values."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(
            tuple(sorted((str(key), freeze_json(child)) for key, child in value.items()))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def thaw_json(value: FrozenJsonValue) -> object:
    """Convert immutable domain JSON values into standard encoder inputs."""

    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json(child) for key, child in value.items}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


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
