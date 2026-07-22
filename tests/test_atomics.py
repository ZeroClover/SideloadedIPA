"""Direct contracts for shared serialization and filesystem primitives."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sideloadedipa.util import atomics


def test_canonical_json_and_file_digest_are_stable(tmp_path: Path) -> None:
    payload = atomics.canonical_json({"z": 1, "a": [True, None]})
    path = tmp_path / "payload.json"
    path.write_bytes(payload)

    assert payload == b'{"a":[true,null],"z":1}'
    assert atomics.file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_atomic_write_and_copy_use_private_mode(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "nested" / "destination"

    atomics.atomic_write_bytes(source, b"signed")
    atomics.atomic_copy(source, destination)

    assert destination.read_bytes() == b"signed"
    assert source.stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o600


def test_atomic_write_removes_temporary_file_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("fixture replace failure")

    monkeypatch.setattr(atomics.os, "replace", fail_replace)
    destination = tmp_path / "result"

    with pytest.raises(OSError):
        atomics.atomic_write_bytes(destination, b"content")

    assert list(tmp_path.iterdir()) == []


def test_redaction_prefers_longest_literal_and_recurses() -> None:
    value = {"message": "token-123 token", "items": ["/private/file", 1]}

    assert atomics.redact_value(value, ("token", "token-123", "/private")) == {
        "message": "*** ***",
        "items": ["***/file", 1],
    }


def test_utc_now_is_timezone_aware() -> None:
    assert atomics.utc_now().utcoffset().total_seconds() == 0
