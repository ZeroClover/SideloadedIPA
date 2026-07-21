## Context

### Current pipeline

The production workflow runs on `ubuntu-latest` with Python 3.11 and `uv`. It downloads one provisioning profile per task to `work/profiles/<task_name>.mobileprovision`, rewrites one root `bundle_id`, and invokes `zsign` with one `-m` argument. The same task object also drives release discovery, icon handling, cache updates, R2 publication, registry mutation, and revalidation.

The implementation is concentrated in `scripts/run_signing.py` (1,019 lines) and `scripts/sync_profiles_asc.py` (458 lines). Configuration parsing, external API calls, subprocess execution, domain decisions, logging, retries, and publication side effects are interleaved. The immediate limitation is not an absence of multi-profile support in `zsign`: upstream `zsign` accepts repeated `-m` arguments and maps profiles by the suffix of each profile's `application-identifier`. This repository neither models those profiles nor proves that the resulting per-bundle entitlements are correct.

The repository already has cache and selective-rebuild behavior. The previously in-progress `add-ci-caching-optimization` change was reconciled with commit `084fbbc`, archived on 2026-07-21, and now supplies the baseline capabilities under `openspec/specs/`. Its `github-release-tracking` contract preserves the implemented behavior of selecting the first asset when multiple assets match and logging a warning. This change explicitly modifies that one baseline requirement to fail on zero or multiple matches; it is a deliberate breaking change, not an independent parallel requirement.

Repository code, `HEAD`, and `origin/master` were all at `9e04744` during final planning validation. Implementation must record the then-current reviewed baseline, integrate a newer `origin/master` only if one exists, and rerun characterization, publication, and rollback tests before moving those boundaries.

### Research baseline

The external research and local source/IPA inspection were refreshed on 2026-07-21. Implementation must re-check version and API assumptions before changing pinned dependencies.

- LiveContainer was cloned at commit [`e370a92dfc03ce109ebce00ed4a7cfc64ad1c801`](https://github.com/LiveContainer/LiveContainer/commit/e370a92dfc03ce109ebce00ed4a7cfc64ad1c801), tag 3.8.0, dated 2026-07-17.
- The inspected 3.8.0 assets were `LiveContainer.ipa` (`sha256:b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6`) and `LiveContainer+SideStore.ipa` (`sha256:97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425`). Neither contains a usable embedded provisioning profile; both carry placeholder signatures intended to be replaced by a sideloading tool.
- [`zsign` v1.1.1](https://github.com/zhlynn/zsign/releases/tag/v1.1.1), released 2026-07-16, was the latest stable release. Its current documentation explicitly supports repeated `-m` for extensions, and the release includes relevant DER-entitlement, signature verification, and WWDR G2-G8 fixes. The workflow currently pins v1.0.4.
- [App Store Connect CLI v3.1.1](https://github.com/rorkai/App-Store-Connect-CLI/releases/tag/3.1.1), released 2026-07-20, was the latest stable release. The project has moved from `rudrankriyam/App-Store-Connect-CLI` to `rorkai/App-Store-Connect-CLI`; the workflow currently pins 2.4.0 at the old URL.
- Apple's current account help documents establish that [explicit App IDs](https://developer.apple.com/help/account/identifiers/register-an-app-id/) gate capabilities, [capability changes invalidate existing profiles](https://developer.apple.com/help/account/identifiers/enable-app-capabilities/), [App Group containers are registered separately](https://developer.apple.com/help/account/identifiers/register-an-app-group/), and [managed capabilities can require Apple approval](https://developer.apple.com/help/account/reference/provisioning-with-managed-capabilities/).
- Apple's [development profile procedure](https://developer.apple.com/help/account/provisioning-profiles/create-a-development-provisioning-profile/) binds a profile to one App ID, certificates, and devices. Apple's [extension guidance](https://developer.apple.com/library/archive/documentation/General/Conceptual/ExtensibilityPG/ExtensionCreation.html) requires the containing app and extensions to be signed consistently, while the [code-signing procedure](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html) requires nested code to be signed from the deepest component outward.

All five active production tasks currently omit `release_glob` and therefore use the baseline `*.ipa` default. A live GitHub API audit on 2026-07-21 found exactly one matching IPA in each latest stable release:

| Task | Latest stable tag | Current `*.ipa` match |
| --- | --- | --- |
| JHenTai | `v8.0.14+323` | `JHenTai_8.0.14+323.ipa` |
| Eros FE | `v1.9.2+566` | `Eros-FE_1.9.2+566.ipa` |
| Asspp | `4.2.1` | `Asspp.ipa` |
| PiliPlus | `2.1.0` | `PiliPlus_ios_2.1.0+5109.ipa` |
| StikDebug | `3.1.6` | `StikDebug-3.1.6.ipa` |

That snapshot makes the breaking selector rule safe for today's releases but is not a migration guarantee. The implementation gate must query all five releases again, record candidate names, and either retain the default with evidence or add an explicit selector before enabling fail-closed selection.

### LiveContainer bundle and entitlement inventory

The standard release contains four profile-bearing bundles. The SideStore release contains the same four plus a widget extension. Frameworks and dylibs are additional signable nodes, but do not receive their own App IDs or provisioning profiles.

| Bundle path | Source bundle identifier | Variant | Independent profile | Observed functional entitlement contract |
| --- | --- | --- | --- | --- |
| `Payload/LiveContainer.app` | `com.kdt.livecontainer` | both | yes | HealthKit, Clinical Health Records, HealthKit background delivery, increased memory limit, App Groups, `get-task-allow`, and 128 keychain access groups |
| `PlugIns/LaunchAppExtension.appex` | `com.kdt.livecontainer.LaunchAppExtension` | both | yes | App Groups |
| `PlugIns/LiveProcess.appex` | `com.kdt.livecontainer.LiveProcess` | both | yes | Same high-value entitlement set as the root app, including all 128 keychain groups |
| `PlugIns/ShareExtension.appex` | `com.kdt.livecontainer.ShareExtension` | both | yes | App Groups |
| `PlugIns/LiveWidgetExtension.appex` | `com.kdt.livecontainer.LiveWidget` | SideStore only | yes | Placeholder signature has no entitlement payload; its `ALTAppGroups` metadata references the SideStore group, so the required policy must be explicitly confirmed rather than inferred |

The standard source entitlements name the SideStore and AltStore groups. LiveContainer's current `LCSharedUtils.appGroupID` first checks those known groups, then reads `com.apple.security.application-groups` from its own signed entitlements and uses the first valid group. Therefore a standard build can use a newly registered App Group owned by this Apple team; the pipeline must rewrite the root and relevant extensions consistently and must not assume ownership of the upstream groups.

The root app and `LiveProcess` declare 128 keychain groups (`com.kdt.livecontainer.shared`, followed by `.1` through `.127`). These are a functional feature, not incidental signing metadata: LiveContainer allocates them to containers for keychain separation. Losing them can leave a cryptographically valid but functionally degraded IPA.

### Constraints and stakeholders

- The implementation must remain usable on the Linux GitHub runner and must not require a macOS runner unless a measured, documented backend decision proves it necessary.
- Apple account state, role assignments, managed-capability approvals, certificates, devices, and portal/API availability are external and can change independently of the repository.
- Apple API credentials, P12 material, private profiles, and raw certificate data must never be committed, cached as public artifacts, or logged.
- Existing single-bundle tasks, R2 object paths, the apps registry, and download URLs must remain compatible during migration.
- A signing command returning zero is not sufficient evidence of correctness. The publication decision belongs to the verifier, not the signing tool.
- Operators need a clear boundary between safe idempotent automation and Account Holder/Admin work that cannot or must not be performed by CI.

## Goals / Non-Goals

**Goals:**

- Correctly sign any supported IPA containing multiple profile-bearing bundles and recursively nested code, with LiveContainer 3.8.0 as the first acceptance fixture.
- Preserve or intentionally transform each bundle's functional entitlement contract, prove that every requested value is authorized by its profile, and prove that the signed executable contains the expected result.
- Discover the actual IPA structure before touching Apple resources and reject ambiguous or unplanned structures.
- Automate safe, additive, idempotent App ID, supported-capability, and profile operations while producing exact human prerequisites for everything else.
- Refactor the signing system into a small typed domain core with replaceable I/O adapters and thin CLI/workflow entry points.
- Make retries deterministic, cache decisions complete, diagnostics actionable, and publication atomic with respect to verification.
- Preserve legacy single-bundle behavior while migrating it onto the same planning and verification engine.

**Non-Goals:**

- Circumvent Apple entitlement, program-membership, role, device, or managed-capability restrictions.
- Register or mutate Apple resources through undocumented/private APIs.
- Automatically delete Bundle IDs, disable capabilities, remove App Group relationships, revoke certificates, or delete profiles merely because configuration changed.
- Guarantee that every arbitrary IPA can be re-signed; encrypted App Store binaries, malformed archives, unsupported bundle types, unavailable entitlements, and unapproved capabilities remain explicit blockers.
- Build LiveContainer from source, modify its runtime behavior, or support guest-app extensions inside LiveContainer.
- Publish the SideStore-integrated LiveContainer asset by default. It is analyzed to ensure the architecture is general; the initial canary uses the exact `LiveContainer.ipa` asset.
- Perform the final physical-device functional acceptance automatically.

## Decisions

### 1. Use a typed package with a functional core and imperative adapters

Create an installable `src/sideloadedipa/` package. Frozen dataclasses and enums represent `SigningTask`, `BundleNode`, `BundleGraph`, `EntitlementPolicy`, `AppleResourcePlan`, `ProvisioningProfile`, `SigningPlan`, `Diagnostic`, and stage results. Pure functions perform identifier mapping, capability classification, profile authorization, entitlement normalization/comparison, signing-order calculation, and cache fingerprinting.

Side effects sit behind small `Protocol` interfaces: `SourceRepository`, `ArchiveInspector`, `AppleDeveloperClient`, `CertificateProvider`, `SigningBackend`, `ArtifactStore`, `RegistryPublisher`, and `Clock`. Adapters implement GitHub, App Store Connect CLI/API, zsign, filesystem, R2, and Vercel behavior. CLI modules translate typed errors to exit codes and human/JSON output; they do not contain business rules. Existing `scripts/*.py` entry points remain temporary compatibility wrappers.

Package boundaries are:

```text
src/sideloadedipa/
  domain/          immutable models, policies, pure planners
  config/          TOML decoding, validation, legacy migration
  ipa/             safe archive handling, bundle and entitlement inventory
  apple/           client protocol, ASC adapter, resource reconciliation
  signing/         backend protocol, zsign adapter, signing execution
  verification/    profile, entitlement, signature, package validation
  publication/     R2 registry transaction and revalidation
  application/     staged use cases and pipeline coordination
  cli/             inspect, plan, sync, sign, verify, run commands
```

All subprocess calls use explicit argv arrays, bounded timeouts, captured output, redaction, and `shell=False`. Resource lifetimes use context managers and task-scoped temporary directories. Deep modules raise typed exceptions rather than printing or exiting.

**Alternative considered:** split the two existing scripts into more scripts while retaining shared global state. This reduces file size but leaves domain decisions coupled to process/environment state and does not create a testable signing plan.

**Alternative considered:** introduce a large framework such as Pydantic or a workflow engine immediately. Python 3.11 dataclasses, enums, Protocols, and explicit validators are sufficient and keep runtime/dependency cost low; a new dependency requires a demonstrated need.

### 2. Inventory first, with safe extraction and a deterministic bundle graph

The first task-specific stage downloads exactly one source asset, verifies its digest, and inspects it without mutation. The archive layer rejects absolute paths, parent traversal, NUL names, duplicate normalized paths, links/special entries, excessive entry counts, and excessive compressed or uncompressed sizes. It requires one root `Payload/*.app` and uses a task-scoped temporary directory.

The inspector records every profile-bearing bundle (`.app`, `.appex`, and any later explicitly supported bundle type), every framework/dylib/executable that must be recursively signed, parent-child edges, signing depth, `Info.plist` metadata, executable hashes, source identifiers, embedded-profile presence, and XML/DER entitlements. Unknown executable bundle types or unreadable entitlements are blockers, not warnings. Stable sorting and canonical JSON produce a bundle-graph digest.

Entitlement extraction on Linux is an explicit adapter contract. The implementation phase must prove the selected zsign inspection path or a narrowly scoped Mach-O parser against thin/fat binaries and XML/DER fixtures before relying on it. `codesign` remains an independent macOS oracle in tests, not the production Linux implementation.

**Alternative considered:** derive extension identifiers from the upstream repository or task configuration without opening the IPA. Release assets can differ from source and between variants; the signed artifact is authoritative.

### 3. Add explicit multi-bundle policy while preserving legacy configuration

Release asset matching remains owned by the baseline `github-release-tracking` capability rather than by signing policy. Its modified algorithm evaluates every asset with `fnmatch`: zero matches fail with the pattern and available names, one match is selected and recorded as source evidence, and multiple matches fail with all candidates instead of preserving the current first-match warning behavior. Existing tasks may keep the default `*.ipa` only after the migration audit proves it is unambiguous for their current release.

The root `bundle_id` remains valid. A new optional `tasks.signing` table controls multi-bundle behavior. The planner matches bundle rules by source bundle identifier, never by a basename alone. When a nested source identifier is a descendant of the source root, `preserve-source-suffix` derives the target by replacing the source root prefix with the configured root target. Rules can override any target identifier. Non-descendant nested identifiers require an explicit target.

Every profile-bearing bundle in a multi-bundle task must match exactly one rule or a declared, deterministic default; duplicate matches and unknown bundles fail. Multi-bundle tasks default to `unknown_profile_bundles = "error"`. Frameworks and dylibs are inventoried and signed but are not configured as App IDs.

Each bundle selects one entitlement mode:

- `profile`: use profile entitlements; this is the legacy default and is accepted only when the expected functional contract is satisfied.
- `preserve-source`: normalize team-bound fields and apply declared App Group/identifier rewrites while preserving other source values.
- `template`: use a version-controlled plist with a small allowlist of typed placeholders such as team ID, target bundle ID, App Identifier Prefix, and named App Group aliases.

The expected entitlement document is materialized and hashed during planning. Arbitrary environment interpolation is forbidden. Any intentional entitlement removal must be declared with a rationale; undeclared loss is an error.

An illustrative LiveContainer standard-release configuration is:

```toml
[[tasks]]
task_name = "LiveContainer"
app_name = "LiveContainer"
bundle_id = "io.zeroclover.app.livecontainer"
repo_url = "https://github.com/LiveContainer/LiveContainer"
release_glob = "LiveContainer.ipa"
slug = "LiveContainer"

[tasks.signing]
id_strategy = "preserve-source-suffix"
unknown_profile_bundles = "error"
profile_type = "IOS_APP_DEVELOPMENT"

[tasks.signing.app_groups]
shared = "group.io.zeroclover.livecontainer"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer"
role = "root"
target_bundle_id = "io.zeroclover.app.livecontainer"
required_capabilities = ["APP_GROUPS", "HEALTHKIT", "INCREASED_MEMORY_LIMIT", "KEYCHAIN_SHARING"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/root.entitlements.plist"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.LiveProcess"
required_capabilities = ["APP_GROUPS", "HEALTHKIT", "INCREASED_MEMORY_LIMIT", "KEYCHAIN_SHARING"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/live-process.entitlements.plist"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.LaunchAppExtension"
required_capabilities = ["APP_GROUPS"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/app-group-extension.entitlements.plist"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.ShareExtension"
required_capabilities = ["APP_GROUPS"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/app-group-extension.entitlements.plist"
```

The names are domain-level requirements, not a promise that all capabilities are API-automatable. The resource planner classifies them using the verified API adapter.

**Alternative considered:** infer all desired entitlements from the source signature. Placeholder/ad-hoc signatures can be incomplete or contain another team's identifiers. Source data is evidence, but an explicit policy is required for sensitive multi-bundle tasks.

### 4. Reconcile Apple resources in plan/apply phases with a strict automation boundary

The Apple stage first emits a read-only resource plan. Each operation is classified as `no-op`, `safe-automatic`, `manual-required`, or `blocked`. Apply mode may perform only configured safe-automatic operations. It is idempotent and additive:

- Look up or create an exact explicit Bundle ID for each target identifier.
- Enable supported public-API capabilities only when requested and currently absent.
- Resolve the certificate in the P12 to one App Store Connect certificate using a public-key/fingerprint identity; ambiguity or mismatch fails.
- Create or regenerate one development profile per profile-bearing bundle using the exact Bundle ID, resolved certificate, and normalized enabled iOS device set.
- Download profiles to task/bundle-specific paths and create a redacted manifest containing resource IDs and SHA-256 fingerprints.

Capability disablement, Bundle ID deletion, profile cleanup, certificate revocation, and undocumented endpoints are never part of CI reconciliation. A capability change makes the previous profile stale; the reconciler creates and validates its replacement before signing.

The verified App Store Connect API/CLI supports Bundle ID creation, capability listing/updating for exposed capability types, and profile creation/download. It exposes `APP_GROUPS` and `HEALTHKIT`, but the research did not find a public App Group container-creation endpoint, nor public capability types for increased memory limit or Keychain Sharing. Those operations remain manual unless implementation-time official API documentation proves otherwise.

| Activity | Default owner | Automation behavior |
| --- | --- | --- |
| Join/renew Apple Developer Program, accept agreements, create API key, assign sufficient role | human Account Holder/Admin | validate credentials and role; never attempt enrollment or role changes |
| Download and inventory the exact IPA | CI | fully automatic and read-only |
| Select one asset when a release has multiple IPAs | human config once | CI requires an exact/unambiguous selector thereafter |
| Derive nested target IDs from a root prefix | CI | automatic when the source relationship is unambiguous; explicit overrides are reviewed in config |
| Register explicit Bundle IDs | CI when API role permits | additive and idempotent; otherwise emit exact manual action |
| Enable exposed additive capabilities such as `APP_GROUPS`/`HEALTHKIT` | CI when the official API supports the requested settings | never disable; validate the resulting profile |
| Register a team-owned App Group container | human Account Holder/Admin in Developer Portal/Xcode | CI checks the configured identifier/association and blocks until present |
| Associate App IDs with an App Group | CI only if a documented API operation and contract test exist | otherwise manual; no private API fallback |
| Request/approve managed capabilities such as increased memory limit | human Account Holder, plus Apple approval where required | CI reports prerequisite and checks profile evidence |
| Define Clinical Health Records and HealthKit background-delivery values | human code review | treat them as local entitlement-template values under the HealthKit capability, not separate Portal capabilities; CI validates profile authorization and signed output |
| Create/download/refresh per-bundle profiles | CI | automatic after all prerequisites exist |
| Approve entitlement templates and any intentional entitlement removals | human code review | CI performs exact validation every run |
| Sign, verify, repackage, cache, and publish | CI | automatic only after all gates pass |
| Delete/disable/revoke Apple resources | human outside this pipeline | explicitly excluded from automation |
| Install on a registered physical device and validate LiveContainer behavior | human acceptance | required before enabling production publication |

**Alternative considered:** use browser automation for Portal-only work. It is brittle, difficult to audit, may bypass role/approval expectations, and is inappropriate for unattended signing CI.

### 5. Treat entitlements as a three-way authorization contract

For each profile-bearing node, the verifier compares:

1. the policy-generated expected entitlements,
2. the authorization expressed by the downloaded provisioning profile, and
3. the entitlements embedded in the signed executable.

Comparison is semantic, not raw-plist byte equality. Team and application identifiers must be exact after expansion. Requested booleans must be authorized. Arrays are normalized as sets where ordering is not meaningful. App Groups require exact registered values. Keychain groups may be authorized by a profile wildcard, but the signed executable must contain the exact expected 128 values for LiveContainer. Wildcard interpretation is limited to entitlement keys with documented semantics; unknown wildcard forms fail.

Required values missing from either the profile authorization or signed executable fail. Undeclared signed entitlements, changed scalar values, wrong team prefixes, and unexpected entitlement removal also fail, with explicit allowlists only for understood profile defaults. XML and DER entitlement representations must agree where both exist.

This comparison occurs before signing (expected versus profile), after signing (all three), and before publication using a newly reopened output IPA.

**Alternative considered:** trust the provisioning profile wholesale. Upstream zsign initializes each signing asset's entitlement document from its profile when no `-e` is supplied. A development profile can authorize a broader set than the exact functional values that LiveContainer needs, so this can silently remove its 128 keychain groups or other requested values.

### 6. Keep a replaceable signing backend and use the qualified Linux zsign extension

`SigningBackend` accepts a complete immutable plan and returns a signed artifact plus per-node evidence. [ADR 0001](decisions/0001-signing-backend.md) selects zsign v1.1.1 plus the repository's minimal upstreamable per-profile-entitlement patch on Linux. The adapter passes every profile and entitlement document as an adjacent repeated `-m PROFILE -e ENTITLEMENTS` pair, verifies source/version/features/checksums at startup, and does not parse success from log text alone.

The qualification resolved the original constraint. Unmodified zsign v1.1.1 accepts multiple profiles but only one global `-e`; profile-only signing mapped profiles correctly but failed the exact root/LiveProcess 128-Keychain-Group contract. The selected patch changes only CLI construction so repeated `-e` values populate the already per-profile `ZSignAsset` objects and rejects profile/entitlement count mismatches before signing.

The completed implementation gate used private real-profile fixtures and an independent macOS oracle before package or workflow architecture committed to the selected Linux backend. Its continuing contract criteria are:

- all four standard bundles receive the intended profile and target identifier;
- root and `LiveProcess` contain HealthKit, Health Records, background delivery, increased memory, App Group, `get-task-allow`, and exactly 128 target-team keychain groups;
- Launch and Share contain the intended App Group policy without root-only entitlements;
- XML/DER entitlements, nested signatures, and embedded profiles pass independent verification.

Every criterion passed in development-branch qualification run 29839575241. Future source, patch, toolchain, or backend upgrades must pass the same suite without weakening the verifier. A macOS `codesign` adapter remains a correctness fallback, but moving production to a macOS runner requires a new ADR with fresh cost/runtime evidence.

**Alternative considered:** invoke zsign once per extension and then invoke it again for the root. A recursive root pass can overwrite nested signatures/entitlements, so this is not accepted without a verified non-recursive mode and end-to-end evidence.

### 7. Build and validate the complete signing plan before mutating the archive

The pure planner joins bundle inventory, target-ID policy, Apple resource manifest, profiles, certificate identity, and entitlement expectations. It requires exactly one profile for every profile-bearing node, no unused profile, unique target IDs, a consistent team/certificate, an authorized entitlement contract, and a supported signing backend.

The plan includes a topological signing order. Nested frameworks/dylibs are signed with the same certificate but no profile and no application entitlements. Nested frameworks inside extensions are deeper than the extension executable; extensions are deeper than the root app; the root is always last. Unknown executable code fails discovery or planning rather than being left with an old signature.

Signing runs in a fresh workspace, never changes the downloaded source, writes to a temporary output, and atomically promotes the output only after verification. The plan and result use content hashes rather than mutable filenames as identity.

**Alternative considered:** let the backend rediscover and choose profiles implicitly. Backend discovery can differ from the pipeline's assumptions; an explicit plan makes the choice reviewable, cacheable, and testable.

### 8. Make orchestration staged, observable, and publication-gated

The application layer exposes `inspect`, `plan`, `sync`, `sign`, `verify`, and `run` use cases. `plan` is read-only. `run` executes stages in order and records a state manifest. No Apple mutation occurs before configuration and archive inspection succeed; no signing occurs while manual prerequisites or profile mismatches exist; no upload/registry/revalidation/cleanup occurs before output verification succeeds.

Publication keeps immutable R2 versioned keys and performs registry mutation only after all selected artifacts pass. A task failure leaves the prior registry entry and prior artifact reachable. Temporary uploads are either content-addressed final objects or cleaned without changing the registry.

Each run emits a concise human summary and a schema-versioned, redacted JSON report with source release/asset/digest, graph digest, target identifiers, capability classifications, Apple resource IDs, profile/tool/certificate fingerprints, signing plan digest, verification findings, timings, and publication result. Secret material and raw profiles are excluded.

**Alternative considered:** continue to coordinate stages through environment variables and stdout parsing. Typed JSON/state manifests make retries and local reproduction deterministic and reduce shell coupling.

### 9. Treat cache as an optimization, never an authority

The cache fingerprint includes source asset ID and SHA-256, canonical task/signing policy, bundle-graph digest, entitlement-template digests, target identifiers, Apple resource IDs, profile fingerprints/expiry, certificate fingerprint, normalized device digest, backend/tool versions, and pipeline schema version.

A cache hit can skip downloadable/repeatable work, but cannot bypass lightweight profile-expiry checks, plan validation, reopening/verifying a signed artifact before publication, or manual-prerequisite status. Cache state is updated only after the corresponding stage succeeds and is never saved as a successful signing result after failure.

**Alternative considered:** invalidate all tasks on every Apple/device change. Complete per-task fingerprints allow safe selective work while preserving correctness; a schema-version change intentionally causes a full rebuild.

### 10. Test by layer and retain independent oracles

Before moving code, characterization tests lock down current single-bundle release selection, profile handling, zsign argv, metadata/icon extraction, cache updates, R2 keys, registry merging, and failure behavior. Refactoring proceeds in small vertical slices with parity tests.

The new suite includes:

- pure unit tests for configuration, ID derivation, capability classification, profile authorization, entitlement normalization, graph ordering, fingerprints, and state transitions;
- malicious ZIP and bundle-graph fixtures for traversal, symlinks, duplicate paths, archive bombs, missing roots, duplicate IDs, unknown executable nodes, and XML/DER entitlements;
- recorded/redacted ASC contract fixtures plus adapter tests using argv-level fakes; no live Apple mutation in PR tests;
- synthetic multi-bundle IPA/profile fixtures that exercise different entitlements per extension and nested frameworks/dylibs;
- a pinned LiveContainer 3.8.0 fixture fetched and SHA-256 verified during an opt-in integration job, avoiding redistribution of third-party binaries in this repository;
- Linux zsign verification plus a macOS `codesign` oracle job for fixture releases where practical;
- a manual registered-device acceptance checklist covering install/launch, Launch extension, Share extension, LiveProcess/JIT-less behavior, App Group storage, HealthKit access where approved, and the 128-keychain-group diagnostic.

Coverage is enforced by package rather than the legacy `scripts` path. `pytest`, strict mypy, Black, isort, workflow validation, `openspec validate --strict`, and `git diff --check` remain required gates.

**Alternative considered:** rely on one production workflow run as the integration test. That makes Apple state and publication side effects part of basic correctness testing and gives poor fault isolation.

## Risks / Trade-offs

- **Apple does not authorize one or more LiveContainer entitlements for this team or profile type** → stop at the profile authorization gate, report the exact key/value and official/manual prerequisite, and do not publish a reduced-function build without an explicit reviewed policy change.
- **A future zsign release or runner toolchain breaks the per-profile patch** → fail source/patch/version checks, keep the last qualified backend pin, and re-run the mandatory Linux/macOS contract before upgrading; never weaken entitlement expectations.
- **The source IPA's placeholder signature omits or lies about entitlements** → combine source evidence with version-controlled policy templates and pinned upstream-source review; require explicit policy for sensitive bundles.
- **Apple capability APIs or CLI commands change** → isolate them in one adapter, pin/checksum the CLI, contract-test JSON parsing, verify versions at startup, and fail with a manual action rather than guessing.
- **Automatic Bundle ID/capability creation leaves additive resources after a failed run** → plan before apply, make operations idempotent, record created resource IDs, and never auto-delete; unused additive resources are documented for optional human cleanup.
- **App Group mapping changes LiveContainer behavior** → use the runtime's entitlement fallback only for the standard build, apply the same group to every cooperating bundle, and require physical-device validation before production enablement.
- **128 keychain groups exceed an Apple/account/backend constraint** → exercise a real development profile in the spike and block release if the profile or signed executable does not carry the complete set.
- **A new upstream release changes its bundle graph** → include graph digest in cache, reject unknown profile-bearing bundles, and require a reviewed configuration update rather than silently signing a partial graph.
- **Refactoring disrupts working single-bundle publication** → establish characterization tests, migrate through compatibility wrappers, shadow-run new inspection/verification, and keep a temporary legacy-engine switch until parity is accepted.
- **The combined refactor and multi-bundle feature is too large to review safely as one implementation batch** → execute and accept `tasks.md` one numbered section at a time, preserve a working legacy path at every boundary, and do not start a dependent section until the preceding gate's evidence is recorded.
- **Stricter verification initially rejects currently published apps** → roll out read-only reports first, classify real differences, and require explicit policy rather than adding broad ignores.
- **More profiles/API calls increase runtime and rate-limit exposure** → reconcile only stale resources, cache by immutable fingerprints, batch/list once where supported, use bounded retries with jitter, and surface rate-limit reset data.
- **Detailed diagnostics expose sensitive Apple metadata** → redact secrets and raw profiles, hash certificate/profile data, keep private artifacts retention-limited, and test log redaction.

## Migration Plan

1. **Characterize the reviewed baseline:** record the implementation commit, lock down current first-match/warning behavior and all single-bundle, cache, icon, R2, registry, and rollback behavior, and keep legacy entry points unchanged.
2. **Qualify the signing backend:** completed by Linux/macOS qualification run 29839575241 and accepted ADR 0001, selecting the upstreamable per-profile-entitlement zsign extension on Linux.
3. **Audit source selection and scaffold:** query the latest stable release for all five production tasks, record every `*.ipa` candidate, add explicit selectors where needed, implement the breaking exactly-one-match rule with regression coverage, and introduce the typed package/interfaces against the accepted backend decision.
4. **Read-only inventory, planning, and verification:** add secure IPA inspection, compatibility parsing, identifier/entitlement policies, profile decoding, signing plans, and post-sign reports. Shadow-run all current tasks and resolve every difference without changing Apple resources, signed output, cache success, or publication.
5. **Apple adapter migration:** move existing profile synchronization behind the two-phase reconciler, prove single-bundle parity, then enable additive multi-App-ID/profile planning. Keep apply actions behind an explicit workflow stage.
6. **Qualified backend and multi-bundle canary:** implement the selected backend contract, extend the existing deferred LiveContainer entry with exact `LiveContainer.ipa` selection, create the team-owned App Group and obtain managed/sensitive capability approval manually, generate four validated profiles, sign in a non-publishing job, and complete physical-device acceptance.
7. **Publication enablement:** enable the verified standard LiveContainer task behind a per-task engine flag, update cache fingerprints and workflow pins, then monitor at least one scheduled refresh and one upstream release transition.
8. **Consolidation:** move remaining tasks to the new engine, remove the legacy switch and wrappers only after parity, update operator documentation, and archive this OpenSpec change after all acceptance evidence is recorded.

Rollback disables the new per-task engine/publication flag and returns existing tasks to the legacy single-bundle path. Since Apple mutations are additive, rollback does not delete App IDs, groups, capabilities, or profiles. The last verified registry entry and immutable IPA remain active. A failed LiveContainer canary is never promoted to the registry.

## Open Questions

1. **Resolved:** the real development profiles authorize the intended HealthKit, Health Records, increased-memory, App Group, and wildcard Keychain Group policy; the signed root and LiveProcess carry the exact 128 local values.
2. Can the current official App Store Connect API associate an already registered App Group with an App ID using stable documented settings in v3.1.1? If not, association remains a portal step.
3. **Resolved:** profile-only zsign is insufficient; ADR 0001 selects the qualified repeated `-m/-e` extension.
4. **Resolved for the standard four bundles:** the operator registered `group.io.zeroclover.app.livecontainer` and associated the required capabilities. Clinical Health Records and HealthKit background delivery remain local HealthKit entitlement values, not separate portal capabilities.
5. Should the SideStore-integrated asset become a later separate task? Supporting it requires a fifth profile, a widget-specific entitlement policy, a separate acceptance pass, and an exact `LiveContainer+SideStore.ipa` selector.
