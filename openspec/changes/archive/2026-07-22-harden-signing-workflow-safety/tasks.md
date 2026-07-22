## 1. Manual Workflow Safety

- [x] 1.1 Replace the hand-written LiveContainer canary stages with one non-publishing production run and validate the current run report.
- [x] 1.2 Move qualification private-material cleanup before SSH debug and add static ordering assertions.
- [x] 1.3 Scope shadow, Apple-state probe, Linux qualification, and macOS qualification secrets to only the consuming steps.

## 2. Production Evidence Correctness

- [x] 2.1 Capture production stage start times before adapter work and retain measured stage durations.
- [x] 2.2 Represent unavailable per-node backend timing as null and update canonical report contracts.
- [x] 2.3 Report the complete IPA and icon key set when compensating cleanup fails and align R2 revalidation remediation with transaction rollback.

## 3. Production Failure Isolation and Migration Cleanup

- [x] 3.1 Add parameterized failure injection against `ProductionPipeline` for every visible stage and prove no downstream adapter or side effect runs.
- [x] 3.2 Remove the unused `check_changes` compatibility alias, legacy implementation, tests, fixtures, and stale project documentation.

## 4. Acceptance

- [x] 4.1 Update operator documentation for the non-publishing canary, debug cleanup boundary, nullable node timing, and cleanup diagnostics.
- [x] 4.2 Run formatting, typing, full package coverage, workflow static checks, web tests/build, and strict OpenSpec validation.
- [x] 4.3 Run the credentialed manual multi-bundle canary in CI and retain its production run-report evidence; treat unavailable physical observation as complete with the last verified device acceptance.
