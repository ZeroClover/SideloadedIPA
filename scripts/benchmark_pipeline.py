#!/usr/bin/env python3
"""Benchmark deterministic pipeline fixtures and emit machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from sideloadedipa.adapters.apple import ProfileReconciler  # noqa: E402
from sideloadedipa.cache_decisions import build_cache_index, select_rebuilds  # noqa: E402
from sideloadedipa.domain import (  # noqa: E402
    AppleProfileState,
    SigningPlan,
    VerificationFinding,
    VerificationResult,
)
from sideloadedipa.ipa import discover_bundle_graph, discover_bundle_structure  # noqa: E402
from sideloadedipa.signing_executor import execute_signing_plan  # noqa: E402
from sideloadedipa.signing_planner import build_signing_plan  # noqa: E402
from sideloadedipa.verification import (  # noqa: E402
    build_verification_result,
    required_verification_checks,
)
from tests.test_cache_decisions import fingerprint, record  # noqa: E402
from tests.test_inventory_fixtures import (  # noqa: E402
    FIXTURES,
    FixtureEntitlementInspector,
    MarkerMachOProbe,
    load_fixture,
    materialize_fixture,
)
from tests.test_profile_reconciliation import (  # noqa: E402
    FakeGateway,
    FakeValidator,
    state,
    sync_request,
)
from tests.test_signing_executor import (  # noqa: E402
    CopyingBackend,
    certificate,
    plan_for,
    source_ipa,
)
from tests.test_signing_planner import valid_request  # noqa: E402


class CountingProfileGateway(FakeGateway):
    def __init__(self) -> None:
        content = b"valid-newest"
        profile = state("PROFILE_NEWEST", "LiveContainer Dev", content)
        super().__init__((profile,), {profile.resource_id: content})
        self.list_calls = 0

    def list(self) -> tuple[AppleProfileState, ...]:
        self.list_calls += 1
        return super().list()


class BenchmarkVerifier:
    def verify(self, plan: SigningPlan, signed_ipa: Path) -> VerificationResult:
        artifact_sha256 = hashlib.sha256(signed_ipa.read_bytes()).hexdigest()
        findings = tuple(
            VerificationFinding(path, check.replace("*", "arm64"), True)
            for path, check in required_verification_checks(plan)
        )
        return build_verification_result(plan, artifact_sha256, findings)


def _measure(action: Callable[[], object], iterations: int) -> dict[str, object]:
    timings: list[float] = []
    peaks: list[int] = []
    action()
    for _ in range(iterations):
        tracemalloc.start()
        started = time.perf_counter()
        action()
        timings.append((time.perf_counter() - started) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
    return {
        "iterations": iterations,
        "median_ms": round(statistics.median(timings), 3),
        "minimum_ms": round(min(timings), 3),
        "peak_memory_bytes": max(peaks),
    }


def benchmark(iterations: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="sideloadedipa-benchmark-") as directory:
        root = Path(directory)
        inventory_root = root / "inventory"
        materialize_fixture(inventory_root, load_fixture("livecontainer-standard.json"))
        probe = MarkerMachOProbe()
        inspector = FixtureEntitlementInspector()

        def inventory_before() -> object:
            discover_bundle_structure(inventory_root, macho_probe=probe)
            return discover_bundle_graph(
                inventory_root,
                "a" * 64,
                macho_probe=probe,
                entitlement_inspector=inspector,
            )

        def inventory_after() -> object:
            return discover_bundle_graph(
                inventory_root,
                "a" * 64,
                macho_probe=probe,
                entitlement_inspector=inspector,
            )

        planning_request = valid_request()
        profile_gateway = CountingProfileGateway()
        profile_reconciler = ProfileReconciler(profile_gateway, FakeValidator())
        profile_request = sync_request()

        source = root / "source.ipa"
        destination = root / "signed.ipa"
        source_ipa(source)
        signing_plan = plan_for(source)
        signing_certificate = certificate(root)

        def signing() -> object:
            return execute_signing_plan(
                plan=signing_plan,
                source_ipa=source,
                destination_ipa=destination,
                certificate=signing_certificate,
                backend=CopyingBackend(),
                verifier=BenchmarkVerifier(),
            )

        signing()

        def verification() -> object:
            return BenchmarkVerifier().verify(signing_plan, destination)

        current_fingerprint = fingerprint("Example", "a")
        cache = build_cache_index((record(current_fingerprint),))

        def cache_hit() -> object:
            return select_rebuilds((current_fingerprint,), cache)

        metrics = {
            "inventory_before_redundant_scan": _measure(inventory_before, iterations),
            "inventory": _measure(inventory_after, iterations),
            "planning": _measure(lambda: build_signing_plan(planning_request), iterations),
            "profile_reuse": _measure(
                lambda: profile_reconciler.ensure(profile_request), iterations
            ),
            "signing": _measure(signing, iterations),
            "verification": _measure(verification, iterations),
            "cache_hit": _measure(cache_hit, iterations),
        }
        before = metrics["inventory_before_redundant_scan"]
        after = metrics["inventory"]
        assert isinstance(before, dict) and isinstance(after, dict)
        before_median = before["median_ms"]
        after_median = after["median_ms"]
        assert isinstance(before_median, (int, float))
        assert isinstance(after_median, (int, float))
        improvement = 100 * (before_median - after_median) / before_median
        return {
            "schema_version": 1,
            "fixture": str(FIXTURES / "livecontainer-standard.json"),
            "metrics": metrics,
            "inventory_tree_scans": {"before": 2, "after": 1},
            "inventory_median_improvement_percent": round(improvement, 1),
            "profile_api_calls": {
                "list_per_iteration": 1,
                "download_per_iteration": 1,
                "create_total": len(profile_gateway.create_calls),
                "observed_list_total": profile_gateway.list_calls,
                "observed_download_total": len(profile_gateway.download_calls),
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    document = benchmark(args.iterations)
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
