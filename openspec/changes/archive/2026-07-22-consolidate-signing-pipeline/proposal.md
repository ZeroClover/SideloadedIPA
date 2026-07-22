## Why

A full review of the scripts-to-package rewrite (recorded in `IMPROVEMENT_PLAN.md`) found migration debt that undermines the new architecture's own guarantees: production publish code still lives in type- and coverage-exempt `legacy/` modules, superseded orchestration layers survive only through their own tests, the patched zsign contract and the assembled production verifier are never executed by automated tests, and several workflow-safety gaps shipped with the migration (manual-dispatch fall-through to production, a revalidation secret in a URL, a fail-open publication default, no cancellation evidence on CI termination).

## What Changes

- Close workflow and configuration safety gaps: reject mode-scoped dispatch inputs without their owning mode instead of falling through to production publish; move the revalidation secret out of the URL; flip `publication_enabled` to a fail-closed default with explicit `true` entries for currently publishing tasks; write the cancellation journal on the CI termination signal; stop persisting repository credentials during SSH debug sessions; stop uploading signed canary IPAs to shared CI artifact storage.
- Remove superseded and dead paths together with the tests that keep them alive: the fixture-only manifest engine (`pipeline_application`, its runner script, and its CI steps), the pre-manifest `package_commands` sign/run command layer, unused `ports` protocols, retired legacy modules (`sync_profiles_asc`, `reconcile_icons`), zero-consumer helpers, and coverage-padding tests.
- Promote the live-but-exempt legacy modules (`r2_store`, `app_icon`) into `adapters/publication/` under strict mypy and the coverage gate; move the qualification tool five-module set out of `legacy/` as a package-owned tool surface; retire the remaining delegator scripts and `_bootstrap`.
- Restructure the package: fold ~35 loose top-level modules into `signing/`, `cache/`, `pipeline/`, `apple/`, and `util/` subpackages; fix the four layering violations (verification/profile-validation import knot, adapter importing a service, cross-module private-helper imports, production dependence on `legacy/`); consolidate duplicated canonical-JSON, atomic-write, digest, diagnostic-serialization, and redaction helpers.
- Raise test fidelity where fakes currently stand in for production: execute the real pinned patched zsign in PR CI against a deterministic multi-bundle fixture; add assembled sign-then-verify composition tests using the production verifier with genuine and tampered signature evidence; run the checksum-pinned real-IPA integration tests on a recurring workflow; use real Mach-O probes in representative discovery tests; validate R2 request shapes against the botocore service model.
- Consolidate CI: extract duplicated install/build blocks into composite actions, drop the unused production zsign release download, cache the patched zsign source build, assert the qualification negative-control outcome, and close pr-checks gaps (blocking `mypy scripts/`, actionlint coverage of composite actions, example-config parse validation).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `signing-workflow-orchestration`: dispatch-mode input gating, cancellation evidence on CI termination signals, migration-compatibility end state (legacy retirement, no exempt production dependencies, single orchestration engine), canary artifact retention limits, debug-session repository-credential hygiene, and a new secret-safe credential transport requirement.
- `signing-task-configuration`: new fail-closed publication enablement requirement (`publication_enabled` defaults to non-publishing).
- `multi-bundle-signing`: the qualified backend contract must be exercised by automated tests running the real pinned patched binary, not only by fake-backend argv conventions.
- `signed-ipa-verification`: the fail-closed publication gate must be proven in an assembled sign-then-verify composition using the production verifier, with a tampered-artifact negative.

## Impact

- `src/sideloadedipa/**`: roughly 1,500 lines removed, `legacy/` emptied, subpackage restructure with import rewrites; console script and `python -m sideloadedipa.apple_state_probe` entry points preserved via shim or synchronized workflow edits.
- `tests/**`: roughly 1,000 lines of padding and dead-path tests removed; shared fixture factories added; new real-binary, composition, and Stubber-based tests.
- `.github/workflows/*.yml` and new `.github/actions/*`: dispatch gating, composite-action consolidation, patched-zsign build caching, negative-control assertion, scheduled integration run.
- `configs/tasks.toml`: explicit `publication_enabled` entries so production publishing behavior is unchanged by the default flip.
- Docs: README, MIGRATION, `docs/security.md`, and operator runbook reconciled with current reality.
- Published artifact identity, task slugs, R2 object layout, and registry semantics remain unchanged.
