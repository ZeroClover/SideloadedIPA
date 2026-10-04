# Design

## Context

The audit (see `docs/refactoring-plan-dedup-and-test-coupling.md`) quantified the duplication with file-and-line evidence:

- **Entitlement keys, five owners.** `domain/entitlements.py:15-18`, `verification/entitlements.py:13-16`, and `signing/profile_validation.py:29-30` each privately declare `_APPLICATION_IDENTIFIER` / `_TEAM_IDENTIFIER` (the first two also `_KEYCHAIN_GROUPS` / `_APP_GROUPS`); `apple/expected_entitlements.py:57-72`, `signing/planner.py:307`, `signing/inputs.py:90`, and `tools/exercise_zsign_backend.py` (8 hits) use bare string literals.
- **Canonical digest, three implementations.** `domain/entitlements.normalize_entitlements` canonicalizes via `_canonical_value` + `json.dumps(..., allow_nan=False)`; `verification/entitlements._digest` goes through `freeze_json`/`thaw_json`; `util/atomics.canonical_json` (no `allow_nan=False`) is combined with inline `hashlib.sha256(...)` at six sites (`pipeline/sign_stage.py:29`, `pipeline/stage_manifests.py:55`, `pipeline/input_manifests.py:274,311`, `pipeline/run_reports.py:231`, `tools/qualify_backend.py:108`). The NaN-handling inconsistency means the same document can in principle digest differently on different paths.
- **Helper duplication.** `domain/entitlements._freeze` duplicates `domain.common.freeze_json` (different error type only); string-array validators exist as `_string_array` (domain), `_string_list` (profile_validation), `_strings` (inputs); `{key: thaw_json(v) for ...}` appears 12 times across 10 files.
- **Parallel stage layers.** `pipeline/sign_stage.py` is imported only by `pipeline/stages/signing.py`; `pipeline/publish_stage.py` only by `pipeline/stages/publication.py`; `pipeline/publication.py` vs `publish_stage.py` vs `stages/publication.py` are three easily confused names.
- **Test-only production code.** `signing/preflight.execute_after_preflight`, `adapters/publication/r2_store.referenced_keys_from_apps`, and `pipeline/stage_manifests.skip_stage` have zero production callers (verified by import-graph analysis plus grep) but each has dedicated tests.
- **Unconstrained doubles.** `tests/conftest.py::FixtureCopyBackend` copies bytes instead of signing; `FixturePassingVerifier` passes every check; both back `test_production_pipeline.py` and `test_pipeline_failure_injection.py` with no conformance assertion against `ports.SigningBackend` or the verifier protocol.

Constraints:

- `tests/test_production_stage_architecture.py` enforces an acyclic `pipeline/stages/` import graph, forbids stage modules from importing `pipeline.production`, and forbids `from sideloadedipa.domain import` inside stages (direct submodule imports only).
- `normalize_entitlements().sha256`, `policy_sha256()`, and stage-manifest digests are durable cache contracts (`pipeline/sign_stage.py::_SIGNING_POLICY_FINGERPRINT_INVARIANTS` documents the compatibility discipline); any serialization refactor must keep byte-identical output for all existing inputs.
- `ErrorCode` values, remediation strings, and `safe_details` keys are an external diagnostic contract and must not change on any path.
- Active changes `harden-and-streamline-project-foundation` and `reuse-held-state-and-verify-once` also modify `signing-workflow-orchestration`; this change adds new requirements only and must not rewrite requirements those changes touch.

## Goals / Non-Goals

**Goals:**

- Exactly one definition for each well-known entitlement key, the canonical JSON serializer/digester, and the immutable-JSON thaw helper; all five consuming layers import rather than re-declare.
- No public production function that is reachable only from tests: each is deleted, wired into production, or moved to test support.
- Fixture doubles and production adapters provably satisfy the same port protocols, enforced in the normal test suite.
- One stage layer: `pipeline/stages/` owns stage logic with no legacy flat `*_stage.py` companions.

**Non-Goals:**

- No change to what any signing, verification, or cache decision proves — only where the code lives.
- No restructuring of the three-way verification layers or the qualification tools' behavior.
- No new CI job and no real-backend integration environment; contract tests must run in the ordinary PR test suite without credentials or zsign.
- No changes to requirements owned by the two active changes.

## Decisions

**D1 — Shared entitlement keys live in `domain/entitlement_keys.py`, exported as public constants.**
Domain is the lowest layer all three consumers (domain policy, signing validation, verification comparison) may depend on, and a constants-only module cannot create import cycles. The module is deliberately *not* re-exported through `domain/__init__.py` because the architecture guard forbids bucket imports inside stages; consumers use `from sideloadedipa.domain.entitlement_keys import ...`. Capability-specific keys used by only one module (e.g. healthkit keys in `apple/expected_entitlements.py`) stay local. *Alternative rejected:* placing constants in `domain/common.py` — mixes vocabulary with value mechanics and grows a grab-bag module.

**D2 — Golden-value characterization precedes digest unification.**
Before touching any serializer, record the current `normalize_entitlements` sha256, `policy_sha256`, `stage_manifest_sha256`, and verification `_digest` outputs for every entitlement/manifest document under `tests/fixtures/` as golden values. Unification then proceeds only while those tests stay green; this converts the "durable cache contract" constraint into a mechanical check. `util/atomics.canonical_json` gains `allow_nan=False` only after the golden corpus proves no current document relies on NaN/Infinity serialization.

Acceptance-review correction: the corpus also pins every serializer added to the migration scope during implementation — signing plans, normalized Apple snapshots, integrity structure evidence, profile manifests, bundle graphs, and Apple profile device sets. Adding a `json_sha256` call site to this change without a corresponding stable value in `tests/test_digest_stability.py` is not complete.

**D3 — One `json_sha256` helper in `util/atomics.py`; domain serialization shared via `domain/common.py`.**
The six inline `hashlib.sha256(canonical_json(...))` sites collapse to `json_sha256(document, *, default=None)`. `normalize_entitlements` and verification `_digest` keep their distinct semantic entry points (policy errors vs comparison evidence) but delegate byte serialization to one shared function so canonicalization rules cannot drift. *Alternative rejected:* moving all digest logic into domain — `util/atomics` already depends on domain and is the existing home of `canonical_json`/`file_sha256`.

Acceptance-review correction: the same rule applies to profile-manifest, bundle-graph, and profile-device-set summaries; no production caller may compose `hashlib.sha256(canonical_json(...))` directly.

**D4 — Duplicated helpers are replaced by predicates, not error-raising validators.**
`domain/common.py` gains `is_string_sequence(...)` and `thaw_json_object(...)`; call sites keep their own error mapping so `ErrorCode` and `safe_details` contracts are untouched. `domain/entitlements._freeze` becomes a `try/except TypeError` wrapper of `freeze_json` that raises the existing policy error. This keeps the refactor mechanical and reviewable.

**D5 — Test-only functions get individual dispositions, not blanket deletion.**
- `execute_after_preflight`: production (`stages/source_inventory.py`) already inlines the trivial valid-check; delete the function and its two tests.
- `referenced_keys_from_apps`: unreferenced orphan-key GC helper; delete with its tests unless an R2 GC change is scheduled, in which case move it to `tools/` marked un-wired. Default is deletion (recoverable from history).
- `skip_stage`: first confirm no production path skips stage-manifest recording; if none, delete function and tests; if a skip path exists, wire the production caller instead so the test exercises live code.
Each disposition is recorded in `tasks.md` with the evidence used.

Acceptance-review correction: the permanent gate is a transitive AST call/import graph, not an “any production reference” search. Registered console functions and executable `__main__` module guards are roots; module initialization, top-level functions, and consumed classes are distinct contexts. A dead function or unused class method therefore cannot make its dependencies reachable, and every unreachable public function is reported even when it has no test caller. Import binding follows Python scope rules (including deferred closures, class-body `nonlocal`, and comprehension assignment expressions), while package re-export closure preserves aliases and wildcard exports.

**D6 — Contract tests bind doubles to ports in the ordinary suite.**
`tests/fakes.py` hosts `FixtureCopyBackend` and `FixturePassingVerifier` with a docstring pointing at the conformance gate. New `tests/test_backend_contract.py` runs identical protocol-conformance checks (runtime-checkable `ports.SigningBackend` and the verifier protocol, plus signature-shape assertions) against each fake and against `ZsignBackend`/`PackageVerifier` constructed without executing signing — no credentials, no zsign binary required. Any future port change fails the contract test until fakes and adapters move together.

**D7 — Stage consolidation is a move, not a rewrite, and keeps the acyclic guard green.**
`sign_stage.py` functions move into `pipeline/stages/signing.py` (or a new leaf `pipeline/stages/signing_cache.py` if `signing.py` would exceed ~500 lines; the leaf must not import `signing`). `publish_stage.py` folds into `stages/publication.py`. `pipeline/publication.py` renames to `pipeline/publication_service.py`. External importers (`tools/qualify_backend.py` imports `policy_sha256` from `pipeline.sign_stage`) are updated in the same commit. Architecture-guard tests run before and after.

## Risks / Trade-offs

- **Digest drift invalidating production caches** → D2 golden tests plus byte-for-byte delegation; if any fixture digest would change, the change stops and the serializer difference is resolved first.
- **Large mechanical diffs colliding with the two active changes** → sequence trivial import-only edits (keys, helpers) before the stage move; keep each section independently mergeable.
- **Deleting `skip_stage` removes a seam a future change might want** → disposition requires an explicit production-path check and is recorded; recovery from git history is trivial.
- **Contract tests ossify ports** → acceptable; ports are deliberate boundaries and the failure message names both sides to update.
