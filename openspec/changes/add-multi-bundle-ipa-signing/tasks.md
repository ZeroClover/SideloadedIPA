## 1. Baseline and Characterization

- [x] 1.1 Record the implementation-time LiveContainer release tag, commit, asset names, sizes, and SHA-256 digests, and fail the fixture setup if they differ from the reviewed baseline.
- [x] 1.2 Add characterization tests for current TOML loading, legacy root `bundle_id` behavior, direct URL sources, GitHub release selection, multiple-match first-asset selection plus warning, zero-match errors, and rebuild selection.
- [x] 1.3 Add characterization tests for current certificate normalization, profile lookup/download/regeneration, enabled-device filtering, and missing-profile failures.
- [x] 1.4 Add characterization tests for current zsign argv, signed-IPA metadata/icon extraction, output naming, cache updates, R2 keys, registry merge, revalidation, and stale-object cleanup.
- [x] 1.5 Capture current CLI environment inputs, exit codes, GitHub Actions outputs, and redaction expectations as compatibility fixtures.
- [x] 1.6 Run and record the pre-refactor `pytest`, coverage, strict mypy, Black, isort, workflow, and `git diff --check` baseline; fix or explicitly quarantine only failures that predate this change.
- [x] 1.7 Confirm the archived `add-ci-caching-optimization` baseline still specifies first-match-plus-warning and this change's `github-release-tracking` delta is the sole normative owner of the breaking exactly-one-match behavior before implementation semantics change.
- [x] 1.8 Record `9e04744` as the planning baseline, fetch `origin/master` at implementation start, integrate a reviewed successor only if it has advanced, and rerun icon/R2 publication characterization against the recorded implementation commit.
- [x] 1.9 Query the latest stable release for JHenTai, Eros FE, Asspp, PiliPlus, and StikDebug; record every asset matching their effective default `*.ipa`, and require an explicit reviewed `release_glob` before migration for any task with zero or multiple matches.

## 2. Mandatory Signing-Backend Qualification Gate

- [x] 2.1 Build a synthetic four-bundle IPA and private/sanitized development-profile fixture set in which root, process, and two extensions require deliberately different entitlement documents.
- [x] 2.2 Install checksum-verified zsign v1.1.1 in an isolated qualification job and record its executable hash and supported CLI behavior.
- [x] 2.3 Run zsign with repeated `-m` and no global `-e`, then prove which profile and entitlement document each bundle receives.
- [x] 2.4 Exercise LiveContainer-equivalent root/`LiveProcess` policies, including App Group plus exactly 128 target-team keychain groups, and verify Launch/Share do not inherit root-only entitlements.
- [x] 2.5 Compare Linux output with independent macOS `codesign` XML/DER, embedded-profile, nested-signature, and deepest-first sealing evidence.
- [x] 2.6 Write and accept an ADR selecting upstream zsign, an upstreamable per-bundle-entitlement zsign extension, another Linux backend, or a macOS fallback; include runner cost/runtime consequences, preserve the assertions as the mandatory `SigningBackend` contract suite, update affected design/task assumptions, and do not start section 3 until every assertion passes.

## 3. Typed Package Foundation

- [x] 3.1 Change the build configuration to package `src/sideloadedipa/`, update test/coverage import paths, and prove an editable `uv` install and console entry point work.
- [x] 3.2 Implement frozen domain models and enums for tasks, bundle nodes/graphs, entitlement policies, Apple resources, profiles, signing plans, diagnostics, stage state, and publication results.
- [x] 3.3 Implement typed domain/config/adapter error classes carrying stable codes, task/bundle context, remediation, and safe structured details.
- [x] 3.4 Define small Protocol interfaces for source retrieval, archive inspection, Apple resources, certificate input, the ADR-selected signing backend, verification, artifact storage, registry publication, clock, and filesystem boundaries.
- [x] 3.5 Implement a shared subprocess runner using argv arrays, `shell=False`, timeouts, bounded captured output, environment allowlists, and secret/path redaction, with unit tests for metacharacters and failures.
- [x] 3.6 Add application and CLI skeletons for `inspect`, `plan`, `sync`, `sign`, `verify`, and `run`, with dependency injection and no business logic in argument parsing.
- [x] 3.7 Convert existing `scripts/*.py` entry points into compatibility delegators one at a time while retaining characterization-test behavior.
- [x] 3.8 Enforce strict mypy and package-level coverage on every new module before moving the next behavior out of legacy scripts.

## 4. Signing Configuration and Policy

- [x] 4.1 Implement typed parsing/validation for existing task, R2, source, icon, slug, and root `bundle_id` fields without changing valid legacy configurations.
- [x] 4.2 Implement the optional `tasks.signing` schema for identifier strategy, unknown-bundle policy, profile type, App Group aliases, and per-source-bundle rules.
- [x] 4.3 Implement and unit-test `preserve-source-suffix` target-ID derivation, explicit overrides, syntax validation, non-descendant errors, and collision detection.
- [x] 4.4 Implement exact inventory-to-rule reconciliation with aggregated diagnostics for duplicate rules, absent required bundles, and newly discovered unconfigured bundles.
- [x] 4.5 Implement `profile`, `preserve-source`, and `template` entitlement policies with typed transformations, explicit allowed drops plus rationale, and deterministic expected-document hashes.
- [x] 4.6 Implement restricted entitlement-template loading and typed placeholders for team ID, App Identifier Prefix, target bundle ID, and named App Groups; reject path escape and arbitrary environment interpolation.
- [x] 4.7 Modify the baseline `github-release-tracking` asset resolver to require exactly one match, retain the default `*.ipa` only when unambiguous, list all candidates on failure, and add regression tests for the five production-task audits plus LiveContainer's two same-release IPA assets.
- [x] 4.8 Add configuration fixtures covering a root-only app, multiple extensions, non-descendant IDs, duplicate IDs, App Group remapping, intentional entitlement drops, and the SideStore widget variant.
- [x] 4.9 Ensure all configuration and inventory-policy errors are reported before any Apple resource mutation or signing subprocess starts.

## 5. Safe IPA Inventory

- [x] 5.1 Implement source-asset streaming/download digest verification and immutable task-scoped workspace management.
- [x] 5.2 Implement ZIP preflight and safe extraction checks for absolute/traversal/NUL/duplicate-normalized paths, links/special files, entry count, expanded size, and compression ratio.
- [x] 5.3 Implement exactly-one-root `Payload/*.app` discovery with validated `Info.plist`, executable path, bundle identifier, and version metadata.
- [x] 5.4 Implement recursive graph discovery for profile-bearing apps/extensions and profile-free frameworks, dylibs, and Mach-O executables, including nested frameworks inside extensions.
- [x] 5.5 Implement a Linux entitlement-inspector adapter that decodes XML and DER evidence for thin and fat Mach-O binaries and fails on missing/unreadable evidence instead of assuming empty values.
- [x] 5.6 Implement canonical typed entitlement normalization, raw-evidence hashes, XML/DER disagreement diagnostics, embedded-profile presence, stable node ordering, and graph digests.
- [x] 5.7 Add malicious-archive fixtures for every extraction guard plus malformed plist, missing/duplicate root, duplicate bundle ID, unknown executable type, and invalid entitlement payload cases.
- [x] 5.8 Add synthetic graph fixtures for root app, multiple extensions, nested frameworks/dylibs, and a SideStore-style fifth extension.
- [x] 5.9 Add an opt-in integration fixture that downloads only the pinned LiveContainer assets, verifies reviewed SHA-256 values, and asserts the four- versus five-profile-bearing-bundle inventories.
- [x] 5.10 Complete the `inspect` CLI and redacted canonical JSON/human reports, and shadow-run it against every current production task.

## 6. Apple Resource Planning and Reconciliation

- [x] 6.1 Move App Store Connect command execution and JSON decoding behind a typed adapter with version checks, pagination, bounded retries, error mapping, and recorded/redacted contract fixtures.
- [x] 6.2 Implement read-only Apple state collection for Bundle IDs, exposed capabilities/settings, certificates, enabled iOS devices, and profiles using one normalized snapshot per run.
- [x] 6.3 Implement the pure Apple resource planner and classify every operation as `no-op`, `safe-automatic`, `manual-required`, or `blocked` with bundle-specific remediation.
- [x] 6.4 Implement exact explicit Bundle ID lookup and idempotent additive creation, including lookup-after-timeout and no deletion/rename behavior.
- [x] 6.5 Implement an allowlisted capability registry and additive enablement for documented API-supported settings; classify unsupported, managed, sensitive, destructive, and ambiguous capability work as manual/blocking.
- [x] 6.6 Implement App Group existence/association verification where official APIs expose it, and emit Account Holder/Admin portal instructions without private API or browser automation where they do not.
- [x] 6.7 Extract the public identity/fingerprint from the configured P12 and require an exact single match to a valid Apple development certificate before profile creation.
- [x] 6.8 Replace one-profile-per-task storage with deterministic task/bundle paths and a redacted resource manifest keyed by target identifier and stable Apple resource ID.
- [x] 6.9 Implement profile decoding and validation for team, exact application identifier, type, certificate, enabled-device set, dates/refresh threshold, and entitlement authorization.
- [x] 6.10 Implement idempotent profile reuse/replacement so certificate, devices, capabilities, group relationships, expiry, or entitlement changes generate and validate a replacement without deleting the old profile.
- [x] 6.11 Add adapter and planner tests for insufficient roles, missing agreements, API pagination/rate limits, uncertain create responses, partial previous applies, manual prerequisites, stale profiles, and secret redaction.
- [x] 6.12 Complete `plan` and `sync` CLI reports and prove that plan mode performs no Apple, cache-success, signing, R2, or registry mutation.

## 7. Signing Planner and Executor

- [x] 7.1 Implement a pure join from inventory, bundle policy, Apple resource manifest, profiles, certificate identity, expected entitlements, and qualified backend to a canonical signing plan.
- [x] 7.2 Reject missing/duplicate/unused profiles, target-ID collisions, mixed teams/certificates, unauthorized expected entitlements, unknown signable nodes, and unsupported backend features before archive mutation.
- [x] 7.3 Implement deterministic deepest-first topological ordering for nested frameworks/dylibs, extension subtrees, nested apps, and root-last signing.
- [x] 7.4 Implement exact root and nested `CFBundleIdentifier`/application-identifier transformation from the plan, including explicit nested overrides.
- [x] 7.5 Implement the qualified backend adapter with all per-bundle profiles, per-bundle entitlements, version/checksum enforcement, redacted argv evidence, and typed timeout/nonzero failures.
- [x] 7.6 Implement profile-free framework/dylib signing with the intended identity and empty application entitlements.
- [x] 7.7 Implement copy-on-write signing workspaces, temporary output, atomic result promotion, failure cleanup, and preservation of the downloaded source and prior verified artifact.
- [x] 7.8 Emit canonical plan/result digests and per-node backend evidence without credentials, P12 passwords, private keys, or raw profile payloads.
- [x] 7.9 Run current root-only tasks through the new planner/executor behind a per-task engine flag and satisfy all single-bundle characterization tests.

## 8. Fail-Closed Verification

- [x] 8.1 Implement typed semantic entitlement comparison for scalars, booleans, ordered values, set-like arrays, exact App Groups, team-bound values, and narrowly documented wildcards.
- [x] 8.2 Implement pre-sign expected-versus-profile authorization checks and reject missing values before invoking the backend.
- [x] 8.3 Reopen the signed IPA and extract each executable's XML/DER entitlements independently of backend success output.
- [x] 8.4 Implement three-way expected/profile/signed comparison, unplanned-entitlement detection, exact target-team prefix checks, and explicit profile-default allowlists.
- [x] 8.5 Verify each profile-bearing bundle's target `CFBundleIdentifier`, embedded profile identity, team, certificate authorization, device eligibility, and dates.
- [x] 8.6 Cryptographically verify every planned executable and nested seal, including frameworks/dylibs inside extensions, and fail on stale/ad-hoc/unintended identities.
- [x] 8.7 Re-inventory output and compare graph parity, planned identifiers, executable set, safe archive structure, and protected non-signing payload content.
- [x] 8.8 Implement schema-versioned human/JSON verification reports and a single boolean publication gate derived only from required checks.
- [x] 8.9 Add LiveContainer contract tests for four distinct profiles/IDs, root and `LiveProcess` sensitive entitlements plus exactly 128 keychain groups, and App Group-only Launch/Share policies.
- [x] 8.10 Add SideStore-variant tests requiring a fifth `LiveWidget` profile and its own reviewed policy rather than root-policy inheritance.
- [x] 8.11 Run an independent macOS oracle job for qualification/canary artifacts and investigate every Linux/macOS verification disagreement.

## 9. Orchestration, Cache, and Publication

- [x] 9.1 Implement typed stage manifests and ordered source, inventory, policy, resource-plan/apply, signing-plan, sign, verify, and publish state transitions.
- [x] 9.2 Complete `inspect`, `plan`, `sync`, `sign`, `verify`, and `run` use cases so each consumes predecessor manifests rather than parsing stdout/environment state.
- [ ] 9.3 Implement complete cache fingerprints from source/policy/graph/entitlement/ID/resource/profile/certificate/device/backend/tool/schema inputs.
- [ ] 9.4 Migrate selective rebuild logic to per-task fingerprints and invalidate only affected tasks while forcing full rebuild on fingerprint-schema changes.
- [ ] 9.5 Require current time-sensitive prerequisite/profile checks and full output reopening/verification even when a signed artifact cache key matches.
- [ ] 9.6 Move existing R2 upload, apps registry update, Vercel revalidation, and stale-object cleanup behind an atomic verified-publication service.
- [ ] 9.7 Preserve the previous registry/object on sign, verify, upload, or registry failure, and implement the configured batch-atomic publication policy.
- [ ] 9.8 Implement bounded retry/idempotency rules and cancellation cleanup for reads, additive Apple operations, signing workspaces, uploads, and registry transactions.
- [ ] 9.9 Emit one redacted run report with stage timings, provenance, manual actions, cache decisions, verification evidence, and publication outcome; retain it as a limited CI artifact.
- [ ] 9.10 Add failure-injection integration tests at every stage boundary and assert no forbidden downstream side effect occurs.
- [ ] 9.11 Benchmark inventory, planning, profile reuse, signing, verification, memory, API calls, and cache hits against baseline and remove measured redundant I/O or repeated parsing.

## 10. GitHub Actions and Toolchain Migration

- [ ] 10.1 Update zsign installation to the qualified supported release from `zhlynn/zsign`, verify its published checksum/version, and update PR checks and provenance output.
- [ ] 10.2 Update App Store Connect CLI installation to the verified release from canonical `rorkai/App-Store-Connect-CLI`, verify its checksum/version, and update adapter contract tests.
- [ ] 10.3 Split the workflow into visible preflight/inventory, Apple plan/apply, signing, verification, and publication steps with manifests passed through files rather than ad-hoc outputs.
- [ ] 10.4 Introduce a versioned cache namespace/fingerprint and prevent `if: always()` cache saving from marking failed signing or verification state successful.
- [ ] 10.5 Upload redacted plans/reports on success and failure with retention limits; exclude P12 material, raw profiles, private keys, and extracted IPA workspaces.
- [ ] 10.6 Add read-only shadow mode for all current tasks and a non-publishing multi-bundle canary path before changing production engine selection.
- [ ] 10.7 Validate workflow permissions, concurrency, timeouts, secret exposure, canonical download URLs, checksum failure behavior, and fork/PR safety.
- [ ] 10.8 Run actionlint, YAML formatting checks, shell static checks where configured, and a workflow fixture test before enabling the new job path.

## 11. LiveContainer Prerequisites and Canary

- [ ] 11.1 Extend and uncomment the existing deferred LiveContainer entry in `configs/tasks.toml`, retain `bundle_id = "io.zeroclover.app.livecontainer"`, and add the reviewed exact `LiveContainer.ipa` selector, four bundle rules, target identifier strategy, version-controlled entitlement templates, and publication-disabled state.
- [x] 11.2 **Manual:** choose and register a team-owned App Group identifier, associate it with root/Launch/LiveProcess/Share App IDs as required, and record non-secret evidence in the readiness checklist.
- [ ] 11.3 **Manual:** enable HealthKit, Increased Memory Limit, and Keychain Sharing for root and `LiveProcess`; keep Clinical Health Records and HealthKit background delivery in the reviewed local entitlement templates rather than treating them as separate Portal capabilities.
- [ ] 11.4 Run read-only plan mode and confirm it reports exactly four target App IDs/profiles, the intended App Group mapping, supported automatic changes, and no unresolved/ambiguous bundle.
- [ ] 11.5 Apply safe Apple changes, generate four profiles, and prove each profile authorizes its target policy, certificate, devices, and validity window.
- [ ] 11.6 Produce a private non-publishing canary and pass all automated identifier, profile, entitlement, XML/DER, nested-signature, graph, and package checks.
- [ ] 11.7 **Manual:** install the canary on a registered device and record install/launch, Launch extension, Share extension, LiveProcess/JIT-less, App Group storage, approved HealthKit behavior, and the 128-keychain-group diagnostic results.
- [ ] 11.8 Enable production publication only through a reviewed per-task configuration change after both automated and manual evidence pass.
- [ ] 11.9 Observe one scheduled refresh and one upstream-release transition; verify graph changes block safely and prior published output remains available on failure.
- [ ] 11.10 Keep `LiveContainer+SideStore.ipa` non-publishing until a separate fifth-profile/widget-policy plan and device acceptance are completed.

## 12. Migration, Cleanup, Documentation, and Final Acceptance

- [ ] 12.1 Migrate each existing production task to the package engine one at a time and compare source selection, signed metadata, icon, cache, R2 key, registry, and failure behavior against characterization fixtures.
- [ ] 12.2 Remove duplicated global/environment parsing, API, signing, cache, and publication business logic from legacy scripts after its package replacement and parity tests are accepted.
- [ ] 12.3 Remove the per-task legacy-engine switch and obsolete wrappers only after every configured task passes new-engine production parity and rollback no longer depends on them.
- [ ] 12.4 Update `README.md` and `configs/tasks.toml.example` with the multi-bundle schema, exact-asset rule, identifier derivation, entitlement modes, App Group aliases, and standard/SideStore LiveContainer distinctions.
- [ ] 12.5 Add an operator runbook for inspect/plan/apply/sign/verify/run, automation-versus-human responsibilities, Apple roles/approvals, retries, rollback, profile refresh, and safe optional resource cleanup.
- [ ] 12.6 Add security documentation for archive limits, secret handling, report retention, subprocess isolation, official API-only mutations, dependency pinning, and credential rotation.
- [ ] 12.7 Add troubleshooting examples for multiple release assets, new upstream extensions, missing App Groups, unsupported/managed capabilities, profile authorization mismatches, 128-keychain-group loss, XML/DER drift, and nested-signature failures.
- [ ] 12.8 Run the full unit, fixture, adapter, integration, LiveContainer canary, strict mypy, coverage, formatting, workflow, security, performance, and publication-rollback acceptance matrix.
- [ ] 12.9 Run `openspec validate add-multi-bundle-ipa-signing --strict` and `git diff --check`, confirm no unreviewed Apple or publication side effects occurred during tests, and attach final evidence to the change.
- [ ] 12.10 Archive the OpenSpec change only after implementation, production canary observation, documentation, and rollback acceptance are complete.
