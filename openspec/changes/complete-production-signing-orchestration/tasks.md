## 1. Production Stage Composition

- [x] 1.1 Add a filesystem-backed canonical stage-manifest store with run/task isolation, atomic writes, predecessor validation, and tests.
- [x] 1.2 Refactor package preparation so signing plans can be reconstructed and independently verified without invoking the signing backend.
- [x] 1.3 Implement package-owned inspect/preflight, Apple plan/apply, sign, verify, publish, and end-to-end run orchestration over the real adapters.
- [x] 1.4 Wire the default CLI, including a standalone publication stage, to the production orchestrator and add command/transition tests.

## 2. Preflight and Cache Correctness

- [x] 2.1 Aggregate current-source inventory and policy diagnostics across all selected tasks before Apple mutation, with failure-injection tests proving no downstream side effects.
- [x] 2.2 Add digest-verified cache-index parsing/storage and build complete task fingerprints from current source, graph, policy, profiles, certificate, devices, backend, and schema inputs.
- [x] 2.3 Integrate selective rebuilds and require current prerequisite/profile plus full IPA revalidation on every production cache hit.
- [x] 2.4 Promote cache records only after the configured success boundary and cover first-run, affected-only, schema-change, invalid-hit, and valid-hit behavior.

## 3. Evidence and Safety

- [x] 3.1 Populate actual per-node zsign backend evidence for every planned executable and fail successful production signing when evidence is incomplete.
- [x] 3.2 Bound and redact successful subprocess stdout as well as failure/timeout output, with regression tests.
- [x] 3.3 Connect real stage timings, provenance, cache decisions, verification, publication, and cancellation state to retained production reports.
- [x] 3.4 Add compensating deletion for only newly uploaded unreferenced R2 objects after batch upload, registry, or revalidation failure.

## 4. Workflow and Documentation

- [x] 4.1 Replace production legacy change selection with visible package-owned inventory/preflight, Apple plan/apply, signing, verification, and publication steps that exchange manifest files.
- [x] 4.2 Scope Apple, signing, repository, R2, and revalidation secrets to their minimum workflow steps and prove SSH debug inherits none of them.
- [x] 4.3 Replace the six signing-spec Purpose placeholders and update operator documentation for staged execution, cache-hit evidence, reports, cancellation, and rollback.

## 5. Acceptance

- [x] 5.1 Run formatting, strict typing, package coverage, workflow static checks, web tests/build, and strict OpenSpec validation.
- [ ] 5.2 Run a credentialed development-branch CI batch covering all production tasks without publication or with the existing safe publication policy, as authorized by available credentials.
- [ ] 5.3 Run and retain evidence for a second CI execution with at least one cache hit, confirming full reopen verification and no legacy selection path.
- [ ] 5.4 Reconcile every original review finding against code and CI evidence; treat time-blocked observation-only checks as complete with their last verified evidence and explicit residual risk.
