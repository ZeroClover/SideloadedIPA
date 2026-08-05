"""Small value types shared by domain modules."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias, TypeGuard


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


def thaw_json_object(values: Iterable[tuple[str, FrozenJsonValue]]) -> dict[str, object]:
    """Convert immutable domain JSON value pairs back into a dictionary."""

    return {key: thaw_json(value) for key, value in values}


def is_string_sequence(value: object, *, allow_empty: bool = True) -> TypeGuard[Sequence[str]]:
    """Report whether a value is a non-string sequence of strings."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return False
    return all(isinstance(item, str) and (allow_empty or bool(item)) for item in value)


def canonical_json_bytes(
    document: object,
    *,
    default: Callable[[object], object] | None = None,
) -> bytes:
    """Serialize one JSON-compatible document with deterministic byte output.

    Non-finite numbers are rejected: durable digest contracts must never
    depend on the non-standard NaN/Infinity JSON extensions.
    """

    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
        allow_nan=False,
    ).encode()


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
