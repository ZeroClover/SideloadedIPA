## 1. Baseline and dependency gates

- [x] 1.1 Confirm `simplify-ci-workflows` is archived into the baseline before implementation; stop if its `ci-validation` and orchestration deltas are not the effective specifications.
- [x] 1.2 Capture the clean baseline results for pytest/coverage, Black, isort, strict mypy, web tests/build, actionlint, zizmor, OpenSpec strict validation, dependency audits, and `git diff --check`.
- [x] 1.3 Inventory direct-URL operator configuration, qualification-tool callers, historical-document links, and `.codex`/`.cursor`/`.kimi`/`.pi` consumers; record supported consumers and deletion candidates before changing them.
- [x] 1.4 Measure current production IPA sizes and select a source byte ceiling with documented headroom; select then-current supported Python patch, Node 22 release, and exact uv version after compatibility checks.
- [x] 1.5 Accept the baseline section only after the migration inputs, tool versions, size policy, external callers, and rollback evidence are explicit and no production state has changed.

## 2. Reproducible toolchain and dependency health

- [x] 2.1 Add synchronized repository declarations for the selected Python, Node.js, and uv versions and make consolidated CI verify/install those exact contracts.
- [x] 2.2 Remove the unreachable pre-3.11 `tomli` dependency, update Python lock entries with known fixes, and prove runtime dependencies remain compatible.
- [x] 2.3 Upgrade Next.js and compatible transitive web dependencies where fixes exist; for any remaining blocking advisory, add an advisory-specific owner, reachability rationale, remediation condition, and expiry instead of using `npm audit fix --force`.
- [x] 2.4 Add Dependabot configuration for the supported `uv`, `npm`, and `github-actions` ecosystems while preserving full Action commit digests and adjacent version comments.
- [x] 2.5 Add frozen Python and npm audit commands to the consolidated validation chain with tests or static assertions for reviewed exception behavior.
- [x] 2.6 Remove HTML coverage generation from the default pytest options and add a documented opt-in HTML coverage command without changing measured package scope or the 95 percent gate.
- [x] 2.7 Run and accept the toolchain section with frozen installs, dependency audits, the full Python/web validation stack, workflow static analysis, and proof that validation did not rewrite either lockfile.

## 3. Bounded and verified source downloads

- [x] 3.1 Add downloader tests for HTTPS enforcement, redirect downgrade rejection, declared-length rejection, streamed-limit rejection, temporary-file cleanup, and redacted diagnostics.
- [x] 3.2 Introduce the typed package-owned download policy and enforce its maximum bytes, timeout, chunk, and bounded-attempt values during streaming.
- [x] 3.3 Compare every GitHub download with its selected asset's advertised size and available SHA-256, and retain expected/actual values in canonical source evidence.
- [x] 3.4 Retain the measured SHA-256 when GitHub has no advertised digest and bind all downstream work in that run to the measured value.
- [x] 3.5 Implement bounded retries using a fresh temporary file while preserving release tag, asset ID, URL, size, and digest identity across attempts; reject any retry that requires re-resolution.
- [x] 3.6 Propagate distinct transport-limit, advertised-size, digest, redirect, and retry-exhaustion errors through inspect reports without starting inventory or later side effects.
- [x] 3.7 Run and accept source intake with positive/negative unit tests, a checksum-pinned external fixture test when enabled, strict typing, coverage, and proof that failed downloads leave no readable source artifact.

## 4. Immutable direct URL configuration and caching

- [x] 4.1 Add parser/model tests for required canonical `ipa_sha256`, HTTPS-only `ipa_url`, forbidden GitHub-plus-direct-digest combinations, and migration diagnostics for legacy direct tasks.
- [x] 4.2 Add `ipa_sha256` to the typed direct-source model and make direct-source resolution pass it through the same download digest verifier and source evidence schema.
- [x] 4.3 Migrate repository examples, fixtures, docs, and any discovered operator configuration to HTTPS plus reviewed checksums in the same implementation section.
- [x] 4.4 Include direct URL and digest in complete source/cache fingerprints; rebuild when either changes and allow unchanged identities to enter the existing guarded cache-hit verifier.
- [x] 4.5 Remove unconditional direct-URL rebuild logic and prove that force rebuild, missing/incomplete cache evidence, prerequisite drift, and artifact verification still override reuse.
- [x] 4.6 Keep HTTP test servers available only through explicit injected test transport rather than production task configuration.
- [x] 4.7 Run and accept direct-source migration with parser, fingerprint, cache-hit/cache-reject, end-to-end pipeline, docs/example parsing, and backwards-migration diagnostic tests.

## 5. Validated and explicitly cached web registry

- [x] 5.1 Add web tests for valid registry decoding, malformed roots/entries, duplicate slugs, non-HTTPS URLs, missing production configuration, explicit fixture mode, origin failure, and XML-significant values.
- [x] 5.2 Implement the dependency-free TypeScript registry decoder and make the page and ITMS route consume only its validated `AppEntry` values.
- [x] 5.3 Make the server registry fetch explicitly persistent and tagged with `apps`; retain authenticated header-based `revalidateTag("apps", "max")` behavior and its unauthorized negative test.
- [x] 5.4 Replace empty-registry fallbacks with fail-closed first-load behavior while preserving the last valid cached value during stale-while-revalidate refresh failures.
- [x] 5.5 Add an explicit fixture data mode for local/test/CI builds and a production guard that rejects fixture deployment; synchronize environment documentation and validation workflow inputs.
- [x] 5.6 Verify ITMS manifests are generated only for validated entries, escape XML correctly, use HTTPS artifact URLs, return not found for unknown slugs, and retain revalidation cache headers.
- [x] 5.7 Run and accept the web section with unit tests, type/build checks, locked install, dependency audit, and a request-level cache/revalidation test or documented deployment smoke test.

## 6. Canonical source and inventory manifests

- [x] 6.1 Characterize current inspect/plan/sync/sign/verify/publish source-resolution and inventory calls plus all source, graph, stage, cache, and run-report schemas before refactoring.
- [x] 6.2 Define schema-versioned typed source and inventory manifest documents bound to run ID, task, URL, expected/actual size and digest, graph digest, and predecessor success.
- [x] 6.3 Persist those manifests with the shared canonical atomic writer and reload them into typed values with schema, identity, digest, and file-size validation.
- [x] 6.4 Make plan, sync, and sign consume validated predecessor manifests without re-resolving, downloading, extracting, or re-inventorying an unchanged unsigned source.
- [x] 6.5 Add failure-injection tests for missing, truncated, cross-run, cross-task, unsupported-schema, file-tampered, digest-mismatched, and predecessor-failed evidence.
- [x] 6.6 Prove verification and cache-hit publication still reopen and independently inventory the signed IPA rather than trusting unsigned-input manifests.
- [x] 6.7 Replace non-atomic cache/stage decision writes with canonical atomic persistence and test interruption before promotion.
- [x] 6.8 Run and accept manifest reuse with stage-command tests, failure-injection side-effect assertions, report-schema compatibility, strict typing, coverage, and measured proof that unsigned inventory occurs once per run/task.

## 7. Thin production orchestrator

- [x] 7.1 Create the concrete `pipeline/stages` package and extract source/inventory transaction behavior without changing `ProductionPipeline` callers or result schemas.
- [x] 7.2 Extract Apple plan/synchronization transaction behavior while preserving read-only plan, apply gating, aggregated preflight, side-effect journal, and cancellation evidence.
- [x] 7.3 Extract signing and cache transaction behavior and consolidate the duplicated cache-rejection/cache-miss execute-copy-report-write path into one implementation.
- [x] 7.4 Extract independent verification transaction behavior while preserving every graph, profile, entitlement, signature, package, and cache-hit gate.
- [x] 7.5 Extract publication, compensation, registry, revalidation, cleanup, and report transaction behavior without changing public keys or atomic batch semantics.
- [x] 7.6 Reduce `ProductionPipeline` to command compatibility and ordered coordination using direct typed dependencies; do not add a service container, abstract stage base class, or parallel orchestration engine.
- [x] 7.7 Replace broad internal facade imports in touched modules with leaf-module imports where this clarifies ownership, and verify the internal import graph remains acyclic.
- [x] 7.8 Run and accept each extracted stage independently, then run the full pipeline, cancellation, cache, publication failure-injection, CLI compatibility, strict typing, coverage, and import-cycle checks.

## 8. Fixed signing invariants and configuration reduction

- [x] 8.1 Add migration tests proving `id_strategy`, `unknown_profile_bundles`, and `profile_type` are rejected with precise removal guidance while behavior without them remains unchanged.
- [x] 8.2 Remove the three fields from production/example/fixture TOML and configuration documentation in the same change section.
- [x] 8.3 Simplify parser and domain policy models so preserve-source-suffix derivation, unknown-bundle rejection, and iOS development profiles are internal invariants rather than single-value user enums.
- [x] 8.4 Update planner and Apple profile request composition to consume the invariants directly and remove branches/tests that existed only to parse impossible alternatives.
- [x] 8.5 Prove target identifiers, uncovered-extension failure, App Group mapping, entitlement plans, profile requests, fingerprints, and publication configuration remain byte/semantic compatible after migration.
- [x] 8.6 Run and accept configuration reduction with all parser/planner/profile/preflight tests, both repository TOML files, strict typing, coverage, and a repository search showing the removed keys only in migration history/tests that assert rejection.

## 9. Minimal backend qualification lifecycle

- [x] 9.1 Use the baseline caller inventory to define one supported qualification command and an evidence schema covering fixture, backend, plan, output, oracle, and comparison digests.
- [x] 9.2 Make the command reuse production inspect/plan/sync primitives for Apple prerequisites and remove independent mutation/reset decisions from its execution path.
- [x] 9.3 Retain and document the deterministic multi-bundle real patched-zsign PR test, including a tampered or unsigned negative case.
- [x] 9.4 Retain an operator-run macOS codesign oracle comparison that fails as an unmet gate when its platform or prerequisites are absent and never reports that absence as success.
- [x] 9.5 Add requalification checks for backend version, executable digest, patch set, command shape, entitlement behavior, and supported-platform changes.
- [x] 9.6 Delete qualification wrappers, prerequisite/reset code, and tests proven to duplicate the retained command or production primitives; preserve evidence and fixture consumers.
- [ ] 9.7 Run and accept qualification with Linux real-backend tests, command/report tests, no-credential negative paths, a documented macOS manual gate, strict typing, and repository caller searches.

## 10. Documentation and repository surface cleanup

- [x] 10.1 Rewrite README as a concise overview/quick start and move durable configuration, architecture, operation, security, and troubleshooting details into their focused current documents.
- [x] 10.2 Remove the obsolete query-string revalidation-secret example and delete stale serverless/improvement plans after extracting any still-valid decision or migration instruction.
- [x] 10.3 Keep only current migration guidance required by supported configuration and CLI contracts; rely on Git history for completed historical plans unless an external link requires a short redirect.
- [x] 10.4 Apply the recorded agent-client consumer decision: keep `.codex` canonical, remove unsupported mirrors, or regenerate/equality-check explicitly supported mirrors with existing OpenSpec tooling.
- [x] 10.5 Remove dead modules, wrappers, imports, tests, fixtures, and documentation links exposed by the pipeline/configuration/qualification changes without deleting a supported operational entry point.
- [x] 10.6 Run and accept repository cleanup with documentation link/secret-pattern searches, skill equality or absence checks, CLI/help smoke tests, and before/after tracked-file and line-count evidence.

## 11. Final acceptance and handoff

- [x] 11.1 Run the complete Python suite with the 95 percent terminal coverage gate, Black, isort, and strict mypy over every supported package/script scope.
- [x] 11.2 Run frozen Python and npm dependency audits, confirm every blocking finding is fixed or has a valid owned unexpired exception, then run web tests and the production build.
- [x] 11.3 Run actionlint and strict high-severity zizmor analysis, verify immutable Action pins/version comments, and confirm the production and PR workflow surfaces remain those accepted by `simplify-ci-workflows`.
- [x] 11.4 Run the real patched-zsign contract, all enabled external integration tests, and a local non-publishing production composition; record any macOS/device gate that remains intentionally manual.
- [x] 11.5 Run `openspec validate harden-and-streamline-project-foundation --strict`, `openspec validate --all --strict`, `git diff --check`, configuration parsing, docs-link checks, and internal import-cycle analysis.
- [x] 11.6 Compare CLI commands, exit/error contracts, report schemas, public object keys, registry behavior, cache safety, and failure-injection side-effect traces with the accepted baseline.
- [x] 11.7 Review the final diff for unrelated files, secrets, generated artifacts, obsolete compatibility code, and expired migration scaffolding; accept each completed section before preparing scoped commits.
