## 1. Workflow and Configuration Safety

- [x] 1.1 Exclude mode-scoped dispatch inputs from the production job condition, add a credential-free guard step that fails a dispatch setting `qualification_apply`/`qualification_reset_names` without their owning mode, and assert the gate in the static workflow tests.
- [x] 1.2 Accept the revalidation secret via request header in the web endpoint (transitional dual acceptance), switch `_trigger_revalidation` to header transport, and remove query acceptance after the web deploy.
- [x] 1.3 Flip the `publication_enabled` parser default to `False`, add explicit `publication_enabled = true` to currently publishing tasks in the same commit, and add a test asserting every production task declares the key explicitly.
- [x] 1.4 Install a SIGTERM handler in production orchestration that routes CI cancellation through the existing cancellation-journal path, with a test proving the journal and cancellation report are written.
- [x] 1.5 Set `persist-credentials: false` on every checkout in both workflows and extend the debug-session hygiene tests to cover the repository token.
- [x] 1.6 Remove the signed canary IPA artifact upload, retain redacted qualification evidence and run reports, and reconcile `docs/security.md`.

## 2. Dead-Path Removal

- [x] 2.1 Delete the fixture-only engine loop: `pipeline_application.py`, `scripts/run_workflow_fixture.py`, `tests/test_pipeline_application.py`, and the workflow-fixture steps in both workflows.
- [x] 2.2 Re-home the "publication-disabled task is rejected before signing" invariant onto `ProductionPipeline` tests, then delete the superseded `package_commands` sign/run command layer, `package_runner.run_package_signing`, `inspection.inspect_command` and their private closures, with their dead tests.
- [x] 2.3 Prune `ports.py` to the four live protocols, delete `tests/test_ports.py`, and delete zero-consumer items: `domain.pipeline.StageState`, `retrying.reconcile_additive_once`, the eight test-only canonical/human serialization functions, and `tests/fixtures/baseline/compatibility-contract.json`.
- [x] 2.4 Delete `legacy/sync_profiles_asc.py` and `legacy/reconcile_icons.py` with their delegator scripts and test files; salvage the three release-audit tests from `test_legacy_characterization.py` into a dedicated file.
- [x] 2.5 Delete the four consumer-less delegator scripts (`app_icon`, `r2_store`, `sync_profiles_asc`, `reconcile_icons`), shrink `test_legacy_delegators.py` accordingly, and delete `scripts/benchmark_pipeline.py` with its test.
- [x] 2.6 Delete the coverage-padding tests identified in `IMPROVEMENT_PLAN.md` §1.1 and merge `tests/test_stage_store.py` into `tests/test_manifest_store.py`.

## 3. Legacy Promotion and Qualification Tooling

- [x] 3.1 Move `legacy/r2_store.py` to `adapters/publication/r2_store.py` and `legacy/app_icon.py` to `adapters/publication/icons.py`, wrap the bare `RuntimeError` into the error taxonomy, and update the two production import sites and test modules.
- [x] 3.2 Remove the `legacy/` exclusions from mypy and coverage configuration for the promoted modules and fix any strict-typing fallout.
- [x] 3.3 Move the five qualification modules to `sideloadedipa.tools` under strict mypy, repoint the workflow invocations to `python -m sideloadedipa.tools.<name>`, and consolidate their test cluster.
- [x] 3.4 Retire `scripts/_bootstrap.py`, the remaining delegator scripts, `tests/test_legacy_delegators.py`, and the emptied `legacy/` package with its configuration exclusions.

## 4. Package Restructure and Consolidation

- [x] 4.1 Extract `pipeline/environment.py` (required-env, P12 decode, publication runtime, safe filename, selected-tasks helpers) and remove the cross-module private imports in `production_pipeline`.
- [x] 4.2 Execute the mechanical moves into `signing/`, `cache/`, `pipeline/`, `apple/`, and `util/` per the design target tree, in move-only commits, keeping a `sideloadedipa.apple_state_probe` shim or landing the workflow edit in the same commit.
- [x] 4.3 Fix the layering violations: `profile_validation` imports `verification/entitlements` directly, `adapters/apple/profiles` receives its validator via injection, and capability classification data moves to domain.
- [x] 4.4 Split `apple_commands.py` into backend, expected-entitlements, reporting, and commands modules; extract `production_pipeline` stage helpers (`source_state`, sign-stage cache orchestration, publish stage) behind a thin sequencer.
- [x] 4.5 Consolidate duplicated plumbing into single implementations: canonical JSON, atomic write (fsync semantics unified), file digest, diagnostic serialization, redaction, and unify the two clock conventions.
- [x] 4.6 Convert the CLI-reachable bare `ValueError`/`TypeError` paths in report canonicalization to taxonomy errors, and fix `subprocesses` output capping (head+tail) and the falsy-zero timeout.

## 5. Test Fidelity

- [x] 5.1 Add the `ZSIGN_BIN`-gated real-backend test (deterministic fixture, generated certificate and CMS profiles, real patched zsign, post-sign per-bundle evidence) and wire the patched build with caching into PR CI.
- [x] 5.2 Add the assembled sign-then-verify composition test using the production verifier with genuine signature evidence and the tampered-artifact negative.
- [x] 5.3 Add a scheduled workflow that runs the checksum-pinned integration marker tests (`SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION=1`).
- [x] 5.4 Convert two bundle-graph discovery cases to real thin Mach-O executables with the real LIEF probe, moving the Mach-O builders to `conftest.py`.
- [x] 5.5 Migrate R2 tests from `MagicMock` to `botocore.Stubber` covering upload confirmation, registry round-trip, and stale-key deletion.
- [x] 5.6 Add `plan_factory`/`profile_factory` conftest fixtures, move cross-imported test helpers into `conftest.py`, and slim the LiveContainer verification contract file to the sensitive-key cases.

## 6. CI Consolidation

- [x] 6.1 Extract `install-asc`, `build-patched-zsign` (cached), and `build-qualification-fixture` composite actions and replace the duplicated blocks in both workflows.
- [x] 6.2 Remove the unused production zsign release download, the dead `inputs.debug == true` condition arms, the vestigial `DEBUG` env, and the redundant cache-save clause; reorder notify before SSH hold.
- [x] 6.3 Make the comparison job consume and assert the negative-control summary, and add `ZSIGN_SOURCE_COMMIT`/`ZSIGN_SOURCE_SHA256` to the workflow pin tests.
- [x] 6.4 Close pr-checks gaps: blocking `mypy scripts/`, actionlint coverage of `.github/actions/**`, drop the nonexistent `main` branch filter, add a `tasks.toml.example` parse test, and remove the redundant re-run of covered test files.

## 7. Documentation and Acceptance

- [x] 7.1 Reconcile README (file structure, zsign description, triggers, secrets), MIGRATION.md (scripts-to-package section), the stale pr-checks comment, and the operator runbook (journal is evidence-only; new dispatch guard).
- [x] 7.2 Run formatting, strict typing, full coverage suite, workflow static checks, web tests/build, and `openspec validate consolidate-signing-pipeline --strict`.
- [ ] 7.3 Execute one credentialed non-publishing canary and one scheduled integration run; retain their reports as acceptance evidence.
