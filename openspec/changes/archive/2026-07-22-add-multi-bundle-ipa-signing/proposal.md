## Why

The signing pipeline models an IPA as one application with one explicit App ID and one provisioning profile, so it cannot correctly re-sign applications such as LiveContainer that contain extensions requiring independent identifiers, capabilities, and profiles. The current monolithic scripts also make signing correctness, entitlement preservation, failure recovery, and future extension difficult to reason about or test; this change introduces a fail-closed multi-bundle signing pipeline while restructuring the code around typed, independently testable components.

## What Changes

- Inspect each downloaded IPA and build a deterministic inventory of its root app, nested apps/extensions, frameworks, dylibs, executables, original identifiers, and requested entitlements before any Apple resource is changed.
- Extend task configuration with a backwards-compatible, declarative per-bundle signing policy: root identifier, derived or explicit nested identifiers, capability/entitlement expectations, and App Group mappings.
- **BREAKING:** replace GitHub release tracking's current "first matching asset plus warning" behavior with fail-closed selection: zero or multiple `release_glob` matches fail and list the candidates; exactly one match is required.
- Reconcile one explicit App ID and development provisioning profile per profile-bearing bundle, automatically applying only supported additive capability changes and emitting an actionable manual-setup report for Apple Portal-only, approval-gated, sensitive, destructive, or ambiguous operations.
- Build and validate a complete signing plan before execution, pass all required profiles to a version-pinned signing backend, and recursively sign nested code from the deepest component outward.
- Add fail-closed checks at inventory, profile, signed-bundle, and publication boundaries so missing profiles, identifier mismatches, entitlement loss, invalid signatures, ambiguous release assets, or unplanned bundles prevent publication.
- Include the IPA bundle graph, signing policy, Apple resource identities, profile fingerprints, signing-tool version, and relevant certificate/device state in cache/change-detection inputs.
- Replace the large orchestration scripts with an installable Python package containing typed domain models, configuration, IPA inspection, Apple resource coordination, signing backends, verification, publication, and thin CLI layers; preserve existing single-bundle task behavior through compatibility parsing and wrappers during migration.
- Upgrade and checksum-pin the supported `zsign` and App Store Connect CLI releases, record their versions in diagnostics, and add characterization, unit, fixture, contract, and end-to-end tests including a pinned LiveContainer fixture.

## Capabilities

### New Capabilities

- `ipa-bundle-inventory`: Safely unpack an IPA and produce a deterministic graph of profile-bearing bundles, nested signable code, identifiers, and entitlement requirements.
- `signing-task-configuration`: Define and validate backwards-compatible root and nested-bundle signing policies, including identifier derivation, overrides, and entitlement expectations.
- `apple-signing-resource-sync`: Plan and reconcile explicit App IDs, supported capabilities, certificates/devices, and per-bundle provisioning profiles while separating automatable actions from manual Apple account work.
- `multi-bundle-signing`: Produce a complete profile-to-bundle signing plan and execute nested-code signing in the required order through a replaceable backend.
- `signed-ipa-verification`: Compare expected, provisioned, and signed entitlements and verify identifiers, embedded profiles, signatures, and package integrity before publication.
- `signing-workflow-orchestration`: Run inspection, resource sync, signing, verification, caching, and publication as observable stages with safe retries and a publication gate.

### Modified Capabilities

- `github-release-tracking`: Change `Asset Matching and Download` from selecting the first of multiple glob matches with a warning to rejecting zero or multiple matches and selecting only an unambiguous single asset.

## Impact

- **Configuration:** `configs/tasks.toml` and its example gain optional nested-bundle signing policy fields while existing single-bundle entries remain valid. GitHub-backed tasks now fail when the default or configured glob matches multiple assets; multi-asset releases such as LiveContainer must use an exact `release_glob`.
- **Python:** `scripts/run_signing.py`, `scripts/sync_profiles_asc.py`, cache/change detection, registry publication, and their tests migrate behind a package-oriented API and thin command entry points.
- **CI:** `.github/workflows/sign-and-upload.yml` and PR checks install checksum-pinned tool versions, run preflight/verification stages, preserve diagnostics, and publish only verified artifacts.
- **Apple resources:** A multi-bundle task can create or update several explicit App IDs and profiles. Unsupported, approval-gated, destructive, or App Group container operations remain explicit human prerequisites.
- **Dependencies and fixtures:** The plan targets `zsign` v1.1.1 and App Store Connect CLI v3.1.1 (latest verified releases on 2026-07-21), subject to implementation-time compatibility checks. Tests add sanitized IPA/profile fixtures and a reproducibly pinned LiveContainer release fixture without committing credentials or private profiles.
- **Operations:** The archived `add-ci-caching-optimization` change is the baseline for cache and release tracking; this change explicitly supersedes only its first-match asset-selection rule. Cache keys and diagnostics change, existing published object paths and registry semantics remain stable, and failed signing-engine migration can fall back to the current single-bundle path until parity is accepted.
