"""Locks for reviewed upstream release evidence used by production canaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "baseline"


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


def _verify_asset(path: Path, expected: dict[str, Any]) -> None:
    assert path.stat().st_size == expected["size"], f"unexpected size for {path.name}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected["sha256"], f"unexpected SHA-256 for {path.name}"


def test_livecontainer_release_fixture_records_reviewed_assets() -> None:
    baseline = _load_json("livecontainer-3.8.0.json")

    assert baseline["tag"] == "3.8.0"
    assert baseline["commit"] == "e370a92dfc03ce109ebce00ed4a7cfc64ad1c801"
    assert [(asset["name"], asset["size"], asset["sha256"]) for asset in baseline["assets"]] == [
        (
            "LiveContainer.ipa",
            4_707_271,
            "b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6",
        ),
        (
            "LiveContainer+SideStore.ipa",
            35_403_538,
            "97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425",
        ),
    ]


def test_livecontainer_fixture_setup_rejects_drift(tmp_path: Path) -> None:
    expected = _load_json("livecontainer-3.8.0.json")["assets"][0]
    candidate = tmp_path / expected["name"]
    candidate.write_bytes(b"not the reviewed release asset")

    with pytest.raises(AssertionError, match="unexpected size"):
        _verify_asset(candidate, expected)


def test_current_release_audit_requires_one_match_per_production_task() -> None:
    audit = _load_json("production-release-audit.json")

    assert audit["effective_glob"] == "*.ipa"
    assert [task["task_name"] for task in audit["tasks"]] == [
        "JHenTai",
        "Eros FE",
        "Asspp",
        "PiliPlus",
        "StikDebug",
    ]
    assert all(len(task["matches"]) == 1 for task in audit["tasks"])
