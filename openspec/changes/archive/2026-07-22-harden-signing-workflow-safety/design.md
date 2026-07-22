## Context

The production CLI now enforces a persisted predecessor chain, but the manually dispatched LiveContainer canary still invokes individual commands and asserts the retired result schema. Qualification jobs also decode credentials into runner-local files while allowing SSH debug before cleanup. The production report schema exposes timing values that are not measured around the work, and the strongest stage-failure test targets a fixture orchestrator instead of `ProductionPipeline`.

## Goals / Non-Goals

**Goals:**

- Make the manual canary exercise the same non-publishing orchestration and independent verification as production.
- Ensure an SSH session cannot read decoded qualification material and receives no unrelated job-level secrets.
- Prove every production stage boundary blocks downstream adapters after failure.
- Make timing and compensating-cleanup evidence accurate rather than merely schema-complete.
- Remove the unused legacy change selector after production parity has been accepted.

**Non-Goals:**

- Change daily production task selection, bundle identifiers, signing policy, or artifact identity.
- Attribute one zsign subprocess duration among individual bundle nodes without observable backend timing.
- Remove other legacy compatibility modules that still have supported callers.

## Decisions

1. **Use one non-publishing `run --apply` command for the canary.** This command owns the inspect, plan, sync, sign, and standalone verify sequence under one run ID. The workflow validates the current command result and retained run report, including exactly one LiveContainer task, successful verification, and absent publication. Keeping separate hand-written stage commands was rejected because it duplicates the production transition contract and already drifted once.

2. **Clean disk material before debug and scope secrets per step.** Qualification cleanup remains `always()` but moves before SSH. Non-secret versions and checksums may stay at job scope; Apple certificate/API and GitHub credentials are supplied only to steps that consume them. The macOS keychain and decoded files receive the same cleanup discipline even though that job currently has no SSH step.

3. **Exercise the real production composition in failure tests.** Parameterized tests replace production adapters with recording fakes, inject one failure at each visible boundary, and assert that no later adapter or side effect occurs. Fixture-pipeline tests may remain only if they cover independent domain behavior.

4. **Measure stage intervals around work and report unavailable node timing as null.** Production stage helpers accept an operation start timestamp captured before the adapter work. A single zsign invocation cannot provide honest per-node durations, so `SigningNodeResult.duration_seconds` becomes optional and the canonical report emits null. Fabricating equal shares or sub-millisecond inspection timing was rejected.

5. **Compute compensating cleanup keys once.** The publication service derives the complete unreferenced key tuple, including IPA and icon objects, and uses that same tuple for deletion and failure diagnostics. Revalidation remediation text describes restoration by the transaction owner rather than retaining the just-written registry.

6. **Delete only the obsolete selector compatibility surface.** Remove `scripts/check_changes.py`, its legacy implementation, characterization tests, and compatibility-contract entry after repository searches prove no production or supported tool imports it.

## Risks / Trade-offs

- **Canary performs real Apple apply operations** → Existing apply behavior is additive/idempotent and remains guarded by the explicit manual input; publication is omitted and R2 credentials stay absent.
- **Secret scoping repeats workflow env blocks** → Prefer repetition at the security boundary over job-wide credential availability; static tests prevent regression.
- **Optional node timing changes report consumers** → The field remains present and becomes explicitly nullable, while total backend duration remains measured.
- **Production failure fixtures require more setup** → Share focused builders from existing production tests rather than introducing a second orchestration abstraction.
