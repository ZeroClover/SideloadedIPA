# Pipeline performance benchmark

Recorded on 2026-07-21 against implementation commit `efd4d6a` before the
inventory optimization was committed.

## Reproduction

```sh
uv run python scripts/benchmark_pipeline.py \
  --iterations 50 \
  --output /tmp/sideloadedipa-pipeline-benchmark.json
```

The benchmark ran with Python 3.14.6 on macOS 27.0. It uses the deterministic
`livecontainer-standard.json` four-bundle fixture and one unmeasured warm-up
iteration before every stage. Timing is a local regression signal, not a
production SLA. The signing fixture uses the normal copy-on-write executor with
a deterministic test backend so the measurement isolates orchestration rather
than external cryptographic-tool latency.

## Results

| Stage | Median (ms) | Minimum (ms) | Peak traced memory (bytes) |
| --- | ---: | ---: | ---: |
| Inventory baseline with redundant structural scan | 7.434 | 7.287 | 1,363,118 |
| Inventory after optimization | 4.087 | 3.941 | 1,292,103 |
| Signing-plan construction | 0.349 | 0.343 | 8,316 |
| Existing-profile reuse | 0.018 | 0.018 | 1,970 |
| Copy-on-write signing orchestration | 4.343 | 4.188 | 1,607,378 |
| Verification-contract construction | 0.312 | 0.307 | 13,796 |
| Cache-hit decision | 0.039 | 0.038 | 1,516 |

The successful inventory path previously traversed the extracted IPA tree once
for structural evidence and immediately traversed it again while constructing
the complete bundle graph. Structural evidence is now collected lazily only
when graph discovery fails. This changes the measured success path from two
tree scans to one and improves its median by 45.0% on this fixture without
weakening failure diagnostics.

The existing-profile case performs one list and one profile-content download per
iteration and makes no create call. The observed totals were 51 lists and 51
downloads, including the single warm-up iteration. Cache-hit selection performs
no external API operation.

## Regression coverage

- `tests/test_inspection.py` asserts successful inventory does not invoke the
  fallback structural scan and a graph failure invokes it exactly once.
- `tests/test_benchmark_pipeline.py` runs the benchmark entry point and asserts
  all required stages, memory metrics, scan counts, and zero profile creation.
- `scripts/benchmark_pipeline.py` emits canonical JSON for repeatable comparison
  in final acceptance and future migration work.
