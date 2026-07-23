## 1. asc CLI contract probe (blocking input for D2)

- [x] 1.1 Against pinned `asc` 3.1.1, confirm `profiles list --profile-type IOS_APP_DEVELOPMENT --output json` returns full attributes including `profileContent` for every item, and record the evidence in this change directory
- [x] 1.2 Against pinned `asc` 3.1.1, confirm `profiles view --id <id> --include bundleId,certificates,devices` exists and returns relationship data inline; record the authenticated list/view contract and selected primary path in `design.md`

## 2. Apple read reuse (spec: bounded state collection, App ID and capability reconciliation)

- [x] 2.1 Extend `AppleStateCollector.collect` to accept held `certificates`/`devices`/`bundle_ids`/`capabilities` slices alongside the existing `profiles` parameter, and normalize merged slices identically to enumerated ones (`src/sideloadedipa/adapters/apple/state.py`)
- [x] 2.2 Rewrite `collect_profiles` per the authenticated probe: decode attributes/content from the list, use one included `view` only for relationships, and drop the 3 separate `links` calls (`src/sideloadedipa/adapters/apple/state.py`)
- [x] 2.3 Scope `_capabilities` enumeration to the transaction's managed App IDs and update snapshot-hash tests accordingly (`src/sideloadedipa/adapters/apple/state.py`)
- [x] 2.4 Add snapshot-slice parameters to `BundleIdReconciler.ensure`, deciding existence from held state and merging the validated create response; keep the uncertain-create recovery re-list limited to the bundle-identifier collection (`src/sideloadedipa/adapters/apple/bundle_ids.py`)
- [x] 2.5 Add snapshot-slice parameters to `CapabilityReconciler.ensure`, decide existence from held state, verify from the documented add response, and remove the unconditional post-add `lookup()` re-list (`src/sideloadedipa/adapters/apple/capabilities.py`)
- [x] 2.6 Replace the successful-create full re-enumeration in `ProfileReconciler.ensure` with a targeted single-resource read of the created profile, merging the verified state into held profiles (`src/sideloadedipa/adapters/apple/profiles.py`)
- [x] 2.7 Retain digest-bound list content in normalized state and make `_validated` reuse those held bytes without another ASC request (`src/sideloadedipa/adapters/apple/profiles.py`)
- [x] 2.8 Restructure `sync_command` to one enumeration per collection: thread the full snapshot through bundle, capability, and profile phases with merge-on-write, and replace the final unconditional `backend.collect()` with the merged snapshot (`src/sideloadedipa/apple/commands.py`)
- [x] 2.9 Update `AppleCommandBackend` protocol and `AscAppleCommandBackend` signatures for the threaded state, keeping fixture backends in tests aligned (`src/sideloadedipa/apple/backend.py`)
- [x] 2.10 Add adapter-call-count assertions proving one initial collection, zero ensure-path re-lists on definite responses, one included relationship view per profile, zero profile `links` calls, zero validation downloads, and one read of immutable collections (`tests/test_apple_commands.py`, `tests/test_profile_reconciliation.py`, `tests/test_bundle_id_reconciliation.py`, `tests/test_capability_reconciliation.py`, `tests/test_apple_state.py`)

## 3. Single-pass, single-extraction verification (spec: signed-ipa-verification, multi-bundle-signing)

- [x] 3.1 Change `VerificationChecks` protocols and the four check implementations to consume a shared extracted tree and precomputed whole-artifact digests (`src/sideloadedipa/verification/service.py`, `artifact.py`, `profiles.py`, `signatures.py`, `integrity.py`)
- [x] 3.2 Extract source and signed artifacts once per `PackageVerifier.verify` pass and thread the trees through the checks; keep finding identities and gate semantics unchanged (`src/sideloadedipa/verification/service.py`)
- [x] 3.3 Remove the complete verifier execution from `execute_signing_plan`, retaining backend result-evidence validation (plan identity, per-node and output digests) as the promotion gate (`src/sideloadedipa/signing/executor.py`)
- [x] 3.4 Move population of the pending cache record's verification fields to the verification stage and keep promotion after verify/publish unchanged (`src/sideloadedipa/pipeline/stages/signing.py`, `src/sideloadedipa/pipeline/stages/verification.py`)
- [x] 3.5 Change cache-hit revalidation to prerequisite + digest checks at the reuse decision, deferring the complete pass to the run's verification stage (`src/sideloadedipa/cache/reuse.py`)
- [x] 3.6 Replace the publish-stage verifier re-execution with validation of the verification-stage manifest, canonical report digest, and artifact digest (`src/sideloadedipa/pipeline/stages/publication.py`)
- [x] 3.7 Update failure-injection and stage tests: verifier executes exactly once per run, publish fails closed on absent or mismatched verification evidence, tampered artifacts still never publish (`tests/test_production_pipeline.py`, `tests/test_pipeline_failure_injection.py`, `tests/test_verification_service.py`, `tests/test_cache_decisions.py`)

## 4. In-run derived-input reuse and CI transaction merge (spec: orchestration additions)

- [x] 4.1 Cache `PreparedContext.plan` so one validated plan instance serves fingerprint, sign, verify, and publish consumers (`src/sideloadedipa/pipeline/stages/models.py:36`)
- [x] 4.2 Make `run()` enter the prepared context once and thread prepared tuples through sign, verify, and publish while preserving the standalone per-stage CLI path (`src/sideloadedipa/pipeline/production.py:292-327`)
- [x] 4.3 Deduplicate P12 decoding so identity and material come from one PKCS#12 parse (`src/sideloadedipa/signing/certificate_identity.py:100-130`)
- [x] 4.4 Memoize `ZsignBackend.identity()` and reuse `plan.backend` during `sign` instead of re-probing (`src/sideloadedipa/adapters/signing/zsign.py:144,186`)
- [x] 4.5 Thread one parsed `TaskConfiguration` through each command invocation instead of re-parsing per helper (`src/sideloadedipa/pipeline/production.py`, `src/sideloadedipa/apple/commands.py:73`)
- [x] 4.6 Reuse already-recorded digests at stage boundaries where the value is in hand: sign-stage artifact hash, verify-stage pre-hash, inventory source hash (`src/sideloadedipa/pipeline/stages/signing.py:278`, `src/sideloadedipa/pipeline/stages/verification.py:124`, `src/sideloadedipa/pipeline/package_runner.py:35`)
- [x] 4.7 Record resource-plan evidence from within `sync --apply` and remove the standalone plan invocation from the production workflow, keeping both plan and apply report documents as CI artifacts (`src/sideloadedipa/pipeline/stages/apple.py`, `.github/workflows/sign-and-upload.yml:97-123`)
- [x] 4.8 Update orchestration tests for combined plan-and-apply evidence and compute-once behavior (`tests/test_production_stage_architecture.py`, `tests/test_apple_command_backend.py`, `tests/test_cli.py`)

## 5. Acceptance and evidence

- [x] 5.1 Run `uv run --frozen pytest`, `black --check`, `isort --check-only`, and `mypy` over the changed packages with all gates green
- [x] 5.2 Run `openspec validate reuse-held-state-and-verify-once --strict` and resolve any findings
- [ ] 5.3 Capture one production scheduled-run report before and after rollout and record in this change directory: asc invocation count (expect ≥85% reduction), verifier executions per task (expect 1), identical plan documents, verification findings, and publication outcome
- [ ] 5.4 Confirm cache-hit and cache-miss paths in production evidence both show single-pass verification with unchanged fail-closed results
