## Why

A structural audit of `src/sideloadedipa` (recorded in `docs/refactoring-plan-dedup-and-test-coupling.md`) found two families of maintainability defects. First, well-known entitlement keys (`application-identifier`, `com.apple.developer.team-identifier`, `keychain-access-groups`, `com.apple.security.application-groups`) are privately re-declared or hard-coded in five production modules, canonical JSON-plus-SHA-256 is implemented three different ways with inconsistent `allow_nan` handling, and freeze/thaw/string-sequence helpers are duplicated across domain, signing, and verification layers. Second, three public production functions (`execute_after_preflight`, `referenced_keys_from_apps`, `skip_stage`) are reachable only from tests, and the production-pipeline tests exercise fixture doubles (`FixtureCopyBackend`, `FixturePassingVerifier`) whose conformance to the real backend/verifier protocols is never asserted — so protocol drift can leave every test green while production wiring breaks.

## What Changes

- Introduce one domain-owned module of shared entitlement key constants and replace private re-declarations and string literals in `domain/`, `verification/`, `signing/`, `apple/`, and `tools/`.
- Add one shared canonical JSON serialization and digest helper and one shared immutable-JSON thaw helper; prove byte-identical digest output with golden-value tests before unifying, because entitlement and policy digests are durable signing-cache contracts.
- Collapse duplicated freeze and string-sequence validators onto shared domain primitives while preserving each call site's error code, remediation, and safe details.
- Fold the legacy flat stage modules (`pipeline/sign_stage.py`, `pipeline/publish_stage.py`) into `pipeline/stages/` and rename `pipeline/publication.py` to `pipeline/publication_service.py`, keeping the stage import graph acyclic and architecture-guard tests green.
- Resolve the three test-only public functions individually: delete `execute_after_preflight`, delete or relocate `referenced_keys_from_apps`, and delete or wire `skip_stage` after confirming whether any production skip path exists.
- Add protocol-conformance contract tests executed against both fixture doubles and the production `ZsignBackend`/`PackageVerifier`, and centralize the doubles in `tests/fakes.py`.
- No behavioral change: CLI surface, configuration schema, signing/verification semantics, cache formats, report schemas, and publication behavior remain identical.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `signing-workflow-orchestration`: Adds single-sourced signing primitives (entitlement keys, canonical digest, JSON thaw) and cohesive stage-module ownership without a parallel flat stage layer.
- `ci-validation`: Adds a protocol-conformance gate requiring test doubles for the signing backend and package verifier to pass the same contract tests as the production implementations.

## Impact

- Code: `src/sideloadedipa/domain/` (new `entitlement_keys.py`, `common.py` helpers), `verification/entitlements.py`, `signing/profile_validation.py`, `signing/inputs.py`, `signing/planner.py`, `apple/expected_entitlements.py`, `tools/exercise_zsign_backend.py`, `pipeline/sign_stage.py`, `pipeline/publish_stage.py`, `pipeline/publication.py`, `pipeline/stages/signing.py`, `pipeline/stages/publication.py`, `adapters/publication/r2_store.py`, `signing/preflight.py`, `pipeline/stage_manifests.py`.
- Tests: golden-value digest fixtures, updated imports, removed dead-code tests, new `tests/fakes.py` and `tests/test_backend_contract.py`.
- No changes to `configs/`, CLI entry points, cache/report schemas, workflows, or the web app.
- Non-goals: no verification-layer restructuring, no error-taxonomy changes, no qualification-tool behavior changes, no new integration environment for the real backend.
