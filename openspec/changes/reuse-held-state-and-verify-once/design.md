# Design

## Context

An audit (2026-07-23) of the production pipeline quantified two families of redundant work.

**Apple reads.** With the current configuration (~8 tasks, ~13 profile-bearing bundles, ~13 development profiles) a steady-state scheduled run performs ~200 `asc` invocations, each a fresh subprocess with its own JWT and TLS session:

- `AppleStateCollector.collect()` costs `4 + N + 4M` calls (N bundle IDs, M profiles): capabilities are enumerated per bundle ID for the whole account, and every profile costs `view` plus three `links` reads even though the JSON:API list response already carries full attributes (including `profileContent`, which the collector hashes and then discards — `adapters/apple/state.py:263,322`).
- `BundleIdReconciler.ensure` re-lists all bundle IDs per target (`adapters/apple/bundle_ids.py:122`) although the sync transaction holds a snapshot; `CapabilityReconciler.ensure` re-lists per capability and again after a successful add (`adapters/apple/capabilities.py:160,191`); `ProfileReconciler.ensure` re-enumerates the entire collection (`1 + 4M` calls) to verify one successful create (`adapters/apple/profiles.py:312`); `sync_command` ends with an unconditional full collect when anything was created (`apple/commands.py:283`); certificates and devices are re-read on every intermediate collect although sync never mutates them.
- The CI workflow runs `plan` and `sync --apply` as separate processes; the plan step's full collect is discarded and its gate (`blocked` ⇒ non-zero exit) is recomputed identically inside `sync --apply`.

**Verification passes.** One `run --apply --publish` executes the complete 5-check `PackageVerifier` three times on the same artifact (inside `execute_signing_plan`, in the verify stage, and again in the publish stage), and four of its checks each perform their own `extract_ipa_safely` — ~12–13 full extractions and >12 whole-file SHA-256 computations of the same bytes per task. `PreparedContext.plan` is a plain property that rebuilds the fully validated signing plan on every access (~5×/run), and sign/verify/publish each rebuild the prepared context (P12 decode ×2 each, one openssl CMS subprocess per profile, ×3 stages). The stage-manifest evidence chain that binds each stage to its predecessor's digests exists but is not relied on for any of this.

Constraints: fail-closed behavior at trust boundaries must not weaken (downloaded IPAs, Apple-issued profiles, restored cache, published output); Apple mutations stay additive; the pinned `asc` 3.1.1 CLI remains the adapter; the active change `harden-and-streamline-project-foundation` already added transaction-scoped **profile** state reuse — this change generalizes it and must not modify requirements that change touches.

## Goals / Non-Goals

**Goals:**

- One enumeration per Apple resource collection per sync transaction; mutation verification from documented API responses or targeted single-resource reads; ≥85% reduction in steady-state `asc` invocations.
- Exactly one complete verifier execution per artifact per run, with one extraction feeding all checks; other stages consume the canonical verification manifest by digest.
- Compute-once semantics inside one process for parsed configuration, prepared signing contexts, signing plans, and backend identity.
- CI executes Apple planning and apply as one transaction recording both evidence stages.

**Non-Goals:**

- No structural merging of verification layers (three-way vs profile checks), no `input_manifests` slim-down, no error-taxonomy changes — future change.
- No direct App Store Connect REST client; the pinned CLI stays.
- No change to what any single verification or validation proves — only how often and at what I/O cost it executes.
- No relaxation of cache-restore distrust: a cache hit still requires prerequisite revalidation plus the run's full verification pass before promotion or publication.

## Decisions

**D1 — Generalize snapshot threading to all reconcilers (mirror of the profile fix).**
`BundleIdReconciler.ensure` and `CapabilityReconciler.ensure` accept the transaction snapshot slices; `sync_command` merges creation results back with the existing `decode_bundle_identifier_response` / `decode_capability_response` decoders and a generalization of `_with_profile_state`. The two intermediate `backend.collect()` calls in `sync_command` disappear; certificates and devices are read once per transaction. *Alternative rejected:* per-reconciler memoized gateways — hides state flow, and the uncertain-create recovery path must still force a genuine re-list of the affected collection.

**D2 — Profile enumeration keeps content; relationships come from one targeted read.**
`collect_profiles` decodes attributes and raw `profileContent` from the list response, then makes one included `view` per profile solely to obtain relationship identifiers. The held list bytes and sha256 flow into `ProfileReconciler._validated`, which validates them without another request. Post-create verification reads only the created resource (`profiles view --id <id> --include bundleId,certificates,devices`) instead of re-enumerating. *Alternative considered:* deriving certificate/device relationships from the embedded `.mobileprovision` payload (zero extra calls); deferred — it changes what the relationship prefilter means, and `MobileProvisionValidator` already provides the authoritative content-level check, so the marginal saving (M targeted reads on the create path only) does not justify the semantic change now.

**D2 probe outcome (2026-07-23) — primary contract authenticated.**
Production debug run
[`29988342462`](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29988342462)
exposed the normal production ASC environment to its SSH child without
credential cleanup. Against the run's pinned `asc` 3.1.1, all 20 development
profile list items carried the complete expected attribute set and non-empty,
strictly decodable `profileContent`. List relationships carried only
`links`/`meta`; `profiles list` has no `--include` option. One
`profiles view --include bundleId,certificates,devices` returned inline
relationship identifiers and matching included resources. The decoded content
from list, included view, and plain view was byte-identical for the sampled
profile. D2 therefore uses list content plus one included relationship view per
profile and removes `_validated`'s extra content read. See
`evidence/asc-3.1.1-profile-contract-probe.md`.

**D3 — Capabilities enumerated only for managed App IDs.**
`snapshot.capabilities` is consumed exclusively through per-bundle filters (`exact_capability_matches`), so collection narrows to the transaction's target bundle IDs. The snapshot hash consequently covers managed scope only; the hash is an in-run evidence binder, not a cross-run identity, so no cache-key migration is needed (cache fingerprints already list profile/certificate/device identities explicitly).

**D4 — Single authoritative verification pass in the verify stage.**
`execute_signing_plan` stops running the verifier; it validates backend result evidence (digests, planned-node evidence) and promotes the artifact. The verify stage runs the one complete pass and fills the pending cache record's verification fields; cache promotion stays where it already is (after verify / at publish). The publish stage validates the VERIFY stage manifest and artifact digest instead of re-running the verifier. Cache-hit revalidation (`revalidate_cached_artifact`) checks prerequisites and digests at decision time and defers full verification to the same run's verify stage — every reused artifact is therefore still reopened and fully verified exactly once before publication. *Alternative rejected:* keeping a second pass at publish "for defense in depth" — it re-proves in the same process, minutes later, what the digest-bound manifest already attests, and doubles the largest I/O cost in the run.

**D5 — One extraction per verification pass.**
`PackageVerifier.verify` extracts source and signed artifacts once and passes extracted trees (plus the one-time whole-file digests) to the four checks; check signatures change accordingly (Protocol update in `VerificationChecks`). Findings, order, and gate semantics are unchanged.

**D6 — Compute-once inside a process.**
`PreparedContext.plan` becomes a cached value; `run()` enters the prepared context once and threads it through sign/verify/publish; `load_configuration` results are threaded instead of re-parsed; `ZsignBackend` memoizes `identity()`; P12 decoding returns identity and material from one decode. Stage CLIs invoked as separate processes are unaffected — canonical manifests remain the cross-process carrier.

**D7 — CI merges the plan step into the apply step.**
`sync --apply` already computes the identical plan and blocks identically; it records both RESOURCE_PLAN and RESOURCE_APPLY evidence so the stage chain is unchanged for downstream `require` calls. The standalone `plan` CLI mode remains for operators.

## Risks / Trade-offs

- [Stale snapshot between enumeration and mutation (TOCTOU widens slightly without intermediate re-lists)] → Mutations remain additive and idempotent; create/add conflicts surface as typed `APPLE_RESOURCE_CONFLICT` and the uncertain-create recovery path still re-lists the affected collection before deciding.
- [`asc` 3.1.1 list/view behavior differs from documented main-branch behavior] → The authenticated D2 probe records the exact pinned-release contract; decoders fail closed if list content, included relationships, or the two content digests disagree.
- [Removing sign-stage verification delays failure detection from sign to verify stage] → Same run, same fail-closed outcome, no side effect occurs between the two stages (cache promotion and publication both sit after verify); backend-evidence validation still catches malformed backend output at sign time.
- [Larger in-memory snapshot (profile content ~10–20 KB × M)] → ~300 KB peak; negligible.
- [Two active changes touch `signing-workflow-orchestration`] → This change only ADDs requirements to that spec and MODIFIES requirements the other change does not touch; archive either change in any order.
- [Evidence semantics: final snapshot hash after creation is built from merged state rather than a fresh full enumeration] → Merged entries originate from documented server responses (create response or targeted read), which is the same provenance class as enumeration; the manifest continues to record resource IDs and digests.

## Migration Plan

1. Land spec deltas (this change), then implement in three PR-sized slices: (a) Apple read reuse (D1–D3, N-series), (b) verification single-pass and single-extraction (D4–D5), (c) compute-once and CI step merge (D6–D7).
2. Each slice keeps behavior-equivalence evidence: existing tests updated to count adapter calls/extractions, production workflow reports compared before/after for identical plan documents, verification findings, and publication outcome. Consecutive manual dispatches on one commit exercise forced-rebuild and cache-hit paths without changing production job semantics.
3. Rollback is per-slice revert; no data-format or registry migration is involved (pending/promoted cache layout unchanged; verification fields of the cache record are simply populated in the verify stage instead of the sign stage).

## Open Questions

- Should the plan document produced inside `sync --apply` continue to be uploaded as a separate CI artifact for audit parity with today's `02-apple-plan.json`? (Default: yes, emit both documents from the single step.)
