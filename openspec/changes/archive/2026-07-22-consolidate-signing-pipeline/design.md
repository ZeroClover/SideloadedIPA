## Context

The multi-bundle rewrite replaced the script pipeline with a package-owned manifest orchestrator, and production now runs through it. The review recorded in `IMPROVEMENT_PLAN.md` (2026-07-22) found the debt left behind: `adapters/publication` and the publish stage import `legacy/r2_store` and `legacy/app_icon`, which are excluded from strict mypy and the coverage gate; the fixture-only `pipeline_application` engine and the pre-manifest `package_commands` command layer survive only through their own tests and CI fixture steps; seven of eleven `ports` protocols have no consumer; the patched zsign binary and the production verifier are never executed together by any automated test; and manual dispatch, secret transport, publication defaults, and cancellation evidence each have a safety gap. The test suite is fast and healthy at the bottom (real Mach-O, real CMS), so changes here are consolidation and fidelity work, not a rebuild.

## Goals / Non-Goals

**Goals:**

- Close the identified workflow and configuration safety gaps with fail-closed behavior.
- Reach the migration end state: no production dependency on `legacy/`, no superseded orchestration layer, no dead protocol seam, no delegator scripts, and no test whose only purpose is keeping dead code covered.
- Give every production-relevant contract at least one automated proof against the real component: patched zsign, assembled verifier, real-IPA inventory, R2 request shapes.
- Restructure the package so module placement communicates layering, and shared plumbing (canonical JSON, atomic writes, digests, diagnostic serialization, redaction) exists once.
- Keep PR CI green at every intermediate step with the existing gates (Black, isort, strict mypy, pytest coverage ≥ 95%, actionlint, workflow tests).

**Non-Goals:**

- Change signing policy, task selection, artifact identity, R2 layout, registry semantics, or the web application contract (beyond the revalidation authentication channel).
- Rework the three report document schemas (run, signing, verification) — only their duplicated plumbing.
- Move production off Linux or change the zsign patch itself.
- Add retries to Apple reconcilers or downloads (recorded as follow-up candidates, not in scope).

## Decisions

1. **Delete the fixture-only engine instead of porting production onto it.** `pipeline_application.ManifestPipelineUseCases`, `scripts/run_workflow_fixture.py`, their tests, and the two CI fixture steps are removed together. The production orchestrator's hand-rolled manifest choreography is a superset (idempotent-replay validation) and is already the only engine production trusts; the alternative — rebasing `ProductionPipeline` onto the generic engine — was rejected because it is a behavior-bearing refactor with no production consumer asking for it, and the stage-transition contract is already proven at the real boundary by the production failure-injection suite.

2. **Promote live legacy modules; migrate the qualification set as a unit.** `legacy/r2_store.py` moves to `adapters/publication/r2_store.py` and `legacy/app_icon.py` to `adapters/publication/icons.py`, both under strict mypy and the coverage gate, absorbing the bare-`RuntimeError` fix into the error taxonomy. The five qualification modules (`build_backend_qualification_fixture`, `qualify_backend_prerequisites`, `exercise_zsign_backend`, `exercise_codesign_oracle`, `compare_backend_qualification`) move to a package-owned `sideloadedipa.tools` subpackage under strict mypy, CI invocations switch to `python -m sideloadedipa.tools.<name>`, and the delegator scripts plus `scripts/_bootstrap.py` are retired. Keeping them as repo scripts was rejected because the delegator/bootstrap mechanism exists only to serve them and blocks emptying `legacy/`.

3. **Restructure with mechanical moves and two shims.** Top-level modules fold into `signing/`, `cache/`, `pipeline/`, `apple/`, and `util/`; `cli`, `application`, `errors`, and a pruned `ports` (four live protocols) stay top-level. Moves are `git mv` plus import rewrites in commits that contain no behavior edits. External entry points constrain two spots: `sideloadedipa.cli:main` stays put, and `python -m sideloadedipa.apple_state_probe` (workflow-invoked) keeps a one-line shim module until the workflow edit lands in the same change. The four layering violations are fixed at their seams: `profile_validation` imports `verification/entitlements` directly; `adapters/apple/profiles` receives its validator instead of importing the service; the shared environment/credential helpers move to `pipeline/environment.py` as public functions, removing cross-module private imports; capability classification data moves down to domain.

4. **`publication_enabled` defaults to non-publishing, flipped atomically.** The parser default becomes `False`, the same commit adds explicit `publication_enabled = true` to every currently publishing task in `configs/tasks.toml`, and a configuration test asserts every production task declares the key explicitly so the default can never silently decide a production task again. Documenting the fail-open default instead was rejected: the documented operating posture (keep new tasks non-publishing until device acceptance) requires fail-closed.

5. **Dispatch inputs are validated before any credentialed job.** The production job condition additionally excludes runs where any mode-scoped input (`qualification_apply`, `qualification_reset_names`) is set, and a credential-free guard step fails the dispatch with an actionable message when a mode-scoped input is set without its owning mode. The static workflow test suite gains assertions for the gate. Relying on operator discipline was rejected after confirming the current gate silently routes such dispatches into a full production publish.

6. **Revalidation authenticates through a header.** The web revalidation endpoint accepts the shared secret in a request header; during the transition it accepts both header and query, the pipeline switches to header-only, then query acceptance is removed. Query-string transport was rejected because the secret persists in Vercel request logs. The macOS `security import` password remains on argv only because the platform tool offers no environment channel; the spec scopes argv exposure to ephemeral, per-run values, and the real P12 password import is documented as the accepted exception with the keychain isolated and destroyed in the same job.

7. **CI cancellation writes the journal.** The orchestrator installs a SIGTERM handler that converts the termination signal into the existing cancellation path so the journal and cancellation report are written under GitHub's cancel escalation, not only on KeyboardInterrupt. The handler only records and re-raises; workspace cleanup semantics are unchanged. The journal remains evidence-only, and that position is now documented in the operator runbook.

8. **Signed canary IPAs stay out of shared artifact storage.** The qualification and canary jobs retain redacted evidence summaries and run reports; the signed IPA artifact upload is removed, restoring the `docs/security.md` contract (embedded profiles expose registered-device UDIDs to anyone with Actions read). Documenting the exposure instead was rejected because no consumer of the uploaded IPA exists beyond ad-hoc download.

9. **Fidelity tests run the real components.** (a) A `ZSIGN_BIN`-gated pytest builds the deterministic multi-bundle fixture, generates a self-signed certificate and CMS profiles with the machinery already in `test_profile_validation`, signs with the real patched backend, and proves per-bundle profile/entitlement selection from post-sign evidence; PR CI builds the patched zsign with a cache keyed on source commit + patch digest and exports `ZSIGN_BIN`. (b) An assembled composition test runs the production verifier against genuine signature evidence built by the existing `test_signature_verification` constructors, with a tampered negative proving fail-closed gating. (c) A scheduled workflow sets the existing integration-marker environment variable to run the checksum-pinned LiveContainer tests. (d) Two discovery tests swap marker probes for the real LIEF probe over minimal real thin Mach-O executables. (e) R2 tests move from `MagicMock` to `botocore.Stubber`. The 95% coverage gate is retained; dead-code removal plus these additions raise effective coverage, and padding-test deletion is sequenced with the dead code it covered so the gate never blocks an intermediate commit.

10. **CI duplication collapses into composite actions.** Following the existing `ssh-debug` precedent: `install-asc`, `build-patched-zsign` (with cache), and `build-qualification-fixture` composite actions replace the six/two/two copies; the unused production zsign release download is dropped; the comparison job consumes and asserts the negative-control summary; `ZSIGN_SOURCE_*` pins join the checksum-count workflow tests; `mypy scripts/` becomes blocking once delegators are gone; actionlint's glob covers `.github/actions/**`.

## Risks / Trade-offs

- [Broad import-rewrite diff obscures review] → Mechanical `git mv`/import-rewrite commits contain no behavior edits and are labeled as such; behavior fixes land in separate commits; the full gate suite runs green at each commit boundary.
- [Default flip could silently disable a publishing task] → Explicit `true` entries land in the same commit as the parser change, plus a test asserting every production task declares `publication_enabled`.
- [Deleting the `package_commands` command layer removes a manual fallback] → The production `run` command covers the same operations; the "publication-disabled task is rejected before signing" invariant is re-homed onto `ProductionPipeline` tests before the deletion commit.
- [PR CI time grows from building patched zsign] → Build cached by source commit + patch digest; cold-cache builds bound the added time (~2 min), warm cache is negligible.
- [Revalidation channel change breaks the deployed site during rollout] → Endpoint accepts both channels during transition; pipeline flips after the web deploy; query acceptance removed last.
- [SIGTERM handler interacts with subprocess teardown] → Handler records the journal and re-raises; it does not attempt cleanup itself; existing `TemporaryDirectory` unwind and subprocess kill behavior are unchanged and covered by tests.
- [Scheduled integration run depends on external release assets] → Assets are checksum-pinned and cached; a download failure fails visibly in the scheduled job without affecting PR CI.

## Migration Plan

Ordered so every step leaves CI green and each is independently revertible:

1. Safety fixes (dispatch gate, secret transport, publication default, SIGTERM journal, checkout credentials, canary artifact) with targeted tests.
2. Dead-path removal (engine loop, command layer, dead protocols, retired legacy modules, zero-consumer helpers, padding tests, doc reconciliation).
3. Legacy promotion (`r2_store`, `app_icon`), qualification-set migration to `sideloadedipa.tools`, delegator retirement, then the package restructure and duplication consolidation.
4. Fidelity additions (real zsign in PR CI, assembled verifier, scheduled integration run, real-probe discovery, Stubber) and test-suite consolidation (conftest factories, cross-test import decoupling).
5. CI consolidation (composite actions, negative-control assertion, pin tests, pr-checks gap closure).

Rollback is `git revert` of the offending step; no data, R2, or registry migration is involved at any step.

## Open Questions

- Whether `reconcile_icons` retirement is acceptable to the operator, or a re-runnable R2 icon-header reconciler should be kept as a package-owned tool. Default in this change: retire it; the live pipeline's `cleanup_stale` covers routine retirement.
