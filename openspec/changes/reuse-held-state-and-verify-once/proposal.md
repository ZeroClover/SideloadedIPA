# Reuse Held State and Verify Once

## Why

A code audit of the production pipeline found systematic re-acquisition of state the run already holds. A steady-state daily run issues roughly 200 App Store Connect CLI invocations where 15–25 would suffice: reconcilers re-list collections that the transaction snapshot already contains, profile enumeration downloads every profile's content and discards it only to download it again during validation, and certificates/devices are re-read although the transaction cannot mutate them. Independently, the complete artifact verifier executes three times per run on identical bytes (sign, verify, publish), safely extracting the same signed IPA ~12–13 times and re-deriving the same publication gate four times, while the canonical evidence chain that exists precisely to carry trust between stages is ignored. These behaviors are partly mandated by current spec wording, so the specs must change before the implementation can.

## What Changes

- Apple state collection becomes bounded per transaction: one enumeration per resource collection, relationship and content data taken from enumeration/creation responses or targeted single-resource reads, capability enumeration scoped to managed App IDs, and no re-reads of collections the transaction cannot mutate.
- Bundle-ID and capability reconcilers consume the transaction snapshot instead of issuing fresh list calls per target; mutation results are verified from the documented create/add response and merged into held state (the pattern the profile reconciler already follows for enumeration).
- Post-create profile verification uses a targeted read of the created resource instead of re-enumerating the entire profile collection.
- Profile content captured during enumeration is retained and reused for validation instead of being re-downloaded.
- The complete independent verifier runs exactly once per artifact per run (the verify stage); the sign stage exposes its artifact after backend-evidence validation, and cache promotion and publication consume the run's canonical verification manifest by digest instead of re-running the verifier.
- One verification pass derives all findings from one safe extraction of the artifact instead of one extraction per check.
- Within one run, immutable derived inputs (parsed configuration, prepared signing context, signing plan, backend identity) are computed once and reused; the CI workflow may execute Apple plan and apply as one transaction that records both evidence stages.
- Out of scope (future changes): structural consolidation of overlapping verification layers, slimming `input_manifests`, and replacing the pinned `asc` CLI with direct REST calls.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `apple-signing-resource-sync`: add a bounded-state-collection requirement; App ID and capability reconciliation decide existence from the transaction snapshot and verify mutations from documented API responses instead of mandatory re-list/re-read.
- `signed-ipa-verification`: add single-authoritative-pass and single-extraction requirements governing how often and how expensively the complete verifier executes per run.
- `signing-workflow-orchestration`: cache-hit revalidation relies on prerequisite/digest checks plus the run's single full verification pass rather than an additional complete verifier execution per consuming stage; add combined plan-and-apply evidence and in-run derived-input reuse requirements.
- `multi-bundle-signing`: result-artifact exposure follows backend completion plus backend-evidence validation, with the complete verification remaining the publication/cache-promotion gate within the same run.

## Impact

- Affected code: `adapters/apple/` (state collector, bundle-ID/capability/profile reconcilers and gateways), `apple/commands.py` and `apple/backend.py` (sync transaction), `verification/service.py` and check modules (shared extraction), `signing/executor.py` (sign-stage verification removal), `pipeline/stages/` (signing/verification/publication evidence flow, prepared-context reuse), `pipeline/production.py` (`run()` composition), `config/parser.py` call sites, `.github/workflows/sign-and-upload.yml` (combined plan/apply step).
- Expected effect: ~90% fewer App Store Connect invocations per scheduled run, one full verifier pass instead of three, one extraction per pass instead of four, minutes saved per CI run, and lower exposure to Apple rate limiting and transient API failures.
- No change to external identifiers, published URLs, registry semantics, task configuration, or the fail-closed posture at trust boundaries (downloaded IPAs, Apple-issued profiles, restored cache, published output).
- Interacts with active change `harden-and-streamline-project-foundation` (transaction-scoped profile state reuse): this change extends the same principle to the remaining collections and stages without modifying any requirement that change touches.
