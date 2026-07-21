"""Smoke test for reproducible pipeline benchmark evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_benchmark_covers_required_stages_and_api_counts(tmp_path: Path) -> None:
    output = tmp_path / "benchmark.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_pipeline.py",
            "--iterations",
            "2",
            "--output",
            str(output),
        ],
        check=True,
    )
    document = json.loads(output.read_text())

    assert set(document["metrics"]) == {
        "inventory_before_redundant_scan",
        "inventory",
        "planning",
        "profile_reuse",
        "signing",
        "verification",
        "cache_hit",
    }
    assert document["inventory_tree_scans"] == {"before": 2, "after": 1}
    assert document["profile_api_calls"]["create_total"] == 0
    assert all(value["peak_memory_bytes"] > 0 for value in document["metrics"].values())
