## 1. Baseline characterization (blocks every later section)

- [x] 1.1 Record the clean baseline: `uv run pytest`, Black, isort, strict mypy, and `openspec validate consolidate-shared-primitives-and-test-fidelity --strict`; confirm the two active changes (`harden-and-streamline-project-foundation`, `reuse-held-state-and-verify-once`) are archived or that this change's edits do not overlap their in-flight files.
- [x] 1.2 Add golden-value tests capturing current `normalize_entitlements().sha256` outputs for every entitlement document under `tests/fixtures/` plus representative inline documents (`tests/test_entitlement_policy.py` additions or a new `tests/test_digest_stability.py`).
- [x] 1.3 Add golden-value tests for `policy_sha256()` (`src/sideloadedipa/pipeline/sign_stage.py`), `stage_manifest_sha256()` (`src/sideloadedipa/pipeline/stage_manifests.py`), and `run_reports` digests against canonical fixtures (`tests/test_stage_manifests.py`, `tests/test_signing_config_fixtures.py` extensions).
- [x] 1.4 Prove the `canonical_json` NaN gap is unexploited: assert no fixture document serializes with NaN/Infinity, then pin `allow_nan=False` behavior in a test (`src/sideloadedipa/util/atomics.py` characterization, `tests/test_atomics.py`).
- [x] 1.5 Accept the section only when all golden tests pass against unmodified production code and the corpus covers every serializer touched in section 4.

## 2. Shared entitlement key constants (spec: signing-workflow-orchestration)

- [x] 2.1 Create `src/sideloadedipa/domain/entitlement_keys.py` with public constants `APPLICATION_IDENTIFIER`, `TEAM_IDENTIFIER`, `KEYCHAIN_ACCESS_GROUPS`, `APPLICATION_GROUPS`, `GET_TASK_ALLOW`; no imports, no `domain/__init__.py` re-export.
- [x] 2.2 Replace private constants in `src/sideloadedipa/domain/entitlements.py` and `src/sideloadedipa/verification/entitlements.py` with imports of the shared constants; delete the private declarations.
- [x] 2.3 Replace private constants in `src/sideloadedipa/signing/profile_validation.py` and string literals in `src/sideloadedipa/signing/planner.py:307` and `src/sideloadedipa/signing/inputs.py:90`.
- [x] 2.4 Replace literals in `src/sideloadedipa/apple/expected_entitlements.py` and `src/sideloadedipa/tools/exercise_zsign_backend.py`; keep healthkit and other single-module keys local.
- [x] 2.5 Verify `grep -rn '"application-identifier"' src` shows only the constants module (any justified remainder documented), and run the entitlement/policy/verification test files.

## 3. Shared domain helpers (spec: signing-workflow-orchestration)

- [x] 3.1 Add `thaw_json_object(values)` to `src/sideloadedipa/domain/common.py` and replace all 12 inline `{key: thaw_json(v) for ...}` occurrences (`pipeline/stages/results.py`, `signing/planner.py`, `signing/service.py`, `signing/profile_validation.py`, `cache/fingerprint.py`, `util/atomics.py`, `adapters/signing/zsign.py`, `verification/three_way.py`, `cli.py`); make `three_way._document` delegate to it.
- [x] 3.2 Add `is_string_sequence(...)` to `src/sideloadedipa/domain/common.py` and re-express `_string_array` (`domain/entitlements.py`), `_string_list` (`signing/profile_validation.py`), and `_strings` (`signing/inputs.py`) on top of it, preserving each site's error type, code, and `allow_empty` behavior.
- [x] 3.3 Replace `domain/entitlements._freeze` with a `try/except TypeError` wrapper over `freeze_json` that raises the existing `ENTITLEMENTS_POLICY_INVALID` error; evaluate replacing `_canonical_value` with freeze/thaw round-trip (`src/sideloadedipa/domain/entitlements.py`). **Outcome:** `_freeze` is now a `try/except TypeError` wrapper over `freeze_json` (with a pre-check preserving the non-string-key policy error); `_canonical_value` was *kept* — a freeze/thaw round-trip would coerce non-string mapping keys via `str(key)` instead of rejecting them, breaking the locked `ENTITLEMENTS_POLICY_INVALID` path for `{42: ...}` documents.
- [x] 3.4 Inline the one-call wrapper `validate_entitlement_authorization` into its caller and delete `verification/profiles._digest` in favor of direct `file_sha256` use (`src/sideloadedipa/signing/profile_validation.py`, `src/sideloadedipa/verification/profiles.py`); update `tests/test_profile_validation.py` imports.
- [x] 3.5 Run domain, signing, and verification test files plus golden tests from section 1; all digests unchanged.

## 4. Canonical digest unification (spec: signing-workflow-orchestration)

- [x] 4.1 Add `json_sha256(document, *, default=None)` to `src/sideloadedipa/util/atomics.py` using the shared canonical serialization.
- [x] 4.2 Replace inline `hashlib.sha256(canonical_json(...))` at `pipeline/sign_stage.py:29`, `pipeline/stage_manifests.py:55`, `pipeline/input_manifests.py:274,311`, `pipeline/run_reports.py:231`, and `tools/qualify_backend.py:108`.
- [x] 4.3 Make `domain/entitlements.normalize_entitlements` and `verification/entitlements._digest` delegate byte serialization to the shared canonical function while keeping their distinct error and evidence semantics.
- [x] 4.4 Run the full golden suite; any byte-level difference stops the section until the serializer discrepancy is resolved and documented in `design.md`.

## 5. Test-only production code resolution (spec: ci-validation)

- [x] 5.1 Confirm via import graph and grep that `execute_after_preflight` (`src/sideloadedipa/signing/preflight.py:127`) has no production caller; delete it and its two tests in `tests/test_preflight.py`, verifying `stages/source_inventory.py` covers the equivalent guard.
- [x] 5.2 Confirm no R2 orphan-GC change is scheduled; delete `referenced_keys_from_apps` (`src/sideloadedipa/adapters/publication/r2_store.py:285`) and its tests in `tests/test_r2_store.py`, recording the disposition in this file. **Disposition:** no R2 orphan-GC change is scheduled (this is the only active change); deleted with its dedicated test class — recoverable from git history. The two `cleanup_stale` tests that derived their referenced set through the helper now pass the expected key set inline.
- [x] 5.3 Check every production stage path for a stage-manifest skip semantic; if none exists, delete `skip_stage` (`src/sideloadedipa/pipeline/stage_manifests.py:250`) and its tests in `tests/test_stage_manifests.py`, otherwise wire the production caller and convert the tests to reach it through the production entry point. **Disposition:** grep evidence shows no production path ever produces a `SKIPPED` stage manifest (`StageStatus.SKIPPED` appears only in `stage_manifests.skip_stage` and the `domain/pipeline.py` enum); function and its two tests deleted.
- [x] 5.4 Delete the stale `scripts/__pycache__/` bytecode of migrated tools and add a `tools/__init__.py` docstring clarifying that `qualify_backend` is a manual operator entry point outside the automated pipeline.
- [x] 5.5 Add a static test (extend `tests/test_production_stage_architecture.py` or new `tests/test_production_reachability.py`) asserting no public function in `src/` is referenced only from `tests/`, with an explicit allow-list for console-script entry points.

## 6. Test-double protocol conformance (spec: ci-validation)

- [x] 6.1 Move `FixtureCopyBackend` and `FixturePassingVerifier` from `tests/conftest.py` into `tests/fakes.py` with a module docstring naming the conformance gate; update `tests/test_production_pipeline.py` and `tests/test_pipeline_failure_injection.py` imports.
- [x] 6.2 Create `tests/test_backend_contract.py` running identical protocol-conformance checks (`isinstance` against runtime-checkable ports plus call-signature shape) over both fakes and over `ZsignBackend`/`PackageVerifier` constructed without executing signing.
- [x] 6.3 Make the verifier-side contract cover the exact protocol `PreparedFactory`/pipeline stages rely on, so a port change fails the contract test before any pipeline test can pass with a stale fake.
- [x] 6.4 Document in `tests/fakes.py` how to update fakes and adapters together when a port changes.

## 7. Stage layer consolidation (spec: signing-workflow-orchestration)

- [x] 7.1 Move `build_fingerprint`, `policy_sha256`, `device_set_sha256`, `template_digests`, `json_digest`, and `restore_cached_signing_report` from `src/sideloadedipa/pipeline/sign_stage.py` into `pipeline/stages/signing.py`, or into a new leaf `pipeline/stages/signing_cache.py` if `signing.py` would exceed ~500 lines; the leaf must not import `stages/signing.py`. **Outcome:** `stages/signing.py` (383 lines) would have exceeded ~500 lines, so the new leaf `pipeline/stages/signing_cache.py` was created; it imports no stage module and the acyclic architecture guard passes.
- [x] 7.2 Update the `policy_sha256` import in `src/sideloadedipa/tools/qualify_backend.py` and delete `pipeline/sign_stage.py`.
- [x] 7.3 Fold `src/sideloadedipa/pipeline/publish_stage.py` into `src/sideloadedipa/pipeline/stages/publication.py` and delete the flat module.
- [x] 7.4 Rename `src/sideloadedipa/pipeline/publication.py` to `pipeline/publication_service.py` and update importers (`pipeline/environment.py`, tests).
- [x] 7.5 Run `tests/test_production_stage_architecture.py` before and after the move, then the full suite, Black, isort, and strict mypy.

## 8. Final acceptance

- [x] 8.1 Full validation: `uv run pytest` with coverage gate, Black, isort, strict mypy, golden digest suite, and `openspec validate consolidate-shared-primitives-and-test-fidelity --strict`.
- [x] 8.2 Demonstrate the conformance gate: temporarily perturb a fake or port in a scratch commit and record the contract test failure, then revert. **Recorded:** renaming `FixturePassingVerifier.verify`'s `signed_ipa` parameter failed `tests/test_backend_contract.py::test_verifier_verify_matches_the_port_call_shape[fixture-double]` (1 failed, 7 passed); reverted to green. The §5.5 reachability gate was likewise demonstrated with a scratch `tmp_probe` module referenced only from a scratch test.
- [x] 8.3 Verify zero behavioral change evidence: identical CLI help output, unchanged report/cache schemas (golden files), and `grep` proof that no private entitlement-key re-declarations remain.
- [x] 8.4 Update `docs/refactoring-plan-dedup-and-test-coupling.md` status markers to reference this change's completed sections.

## 9. Acceptance-review corrections

- [x] 9.1 Add synthetic reachability regressions for unaliased dotted imports, package-relative `__init__.py` re-exports, wildcard imports after function definitions, and value/class `match` patterns; correct import binding and pattern-load resolution.
- [x] 9.2 Add nested `global` and `nonlocal` read/write regressions; make bindings target the module or enclosing function exactly as Python scope declarations require, without pre-binding function-local imports before execution reaches them.
- [x] 9.3 Replace the remaining direct `hashlib.sha256(canonical_json(...))` compositions in profile manifests, bundle graphs, and Apple profile device sets with `json_sha256`.
- [x] 9.4 Extend `tests/test_digest_stability.py` with golden values for every serializer added to the migration scope, including signing plans, Apple snapshots, integrity evidence, profile manifests, bundle graphs, and profile device sets.
- [x] 9.5 Run the focused reachability and digest suites, full pytest coverage gate, Black, isort, strict mypy, and strict OpenSpec validation. **Recorded:** fixed-version `uv 0.11.31` ran 828 tests with 3 opt-in skips and 95.02% coverage; Black and isort checked 199 files; strict mypy passed for 111 package files and 2 script files; strict OpenSpec validation and `git diff --check` passed.

## 10. Entry-reachability review corrections

- [x] 10.1 Replace the any-production-reference check with a transitive graph rooted at registered production entry points; report both test-only and completely unreferenced public functions, and prove a dead wrapper cannot make its target reachable.
- [x] 10.2 Preserve deferred closure semantics for nested functions and lambdas defined before a later enclosing import, while keeping direct pre-import loads unresolved.
- [x] 10.3 Honor class-body `nonlocal` declarations for both rebinding and imports, and bind comprehension assignment expressions to the enclosing non-comprehension scope.
- [x] 10.4 Follow aliased and wildcard package re-exports through bucket imports without treating the re-export itself as a production caller.
- [x] 10.5 Run the focused reachability suite, full pytest coverage gate, Black, isort, strict mypy, strict OpenSpec validation, and `git diff --check`. **Recorded:** the focused resolver suite passed 52 tests; fixed-version `uv 0.11.31` ran 845 tests with 3 opt-in skips and 95.02% coverage; Black and isort checked 199 files; strict mypy passed for 111 package files, 2 script files, and the reachability analyzer; strict OpenSpec validation and tracked/untracked whitespace checks passed.
