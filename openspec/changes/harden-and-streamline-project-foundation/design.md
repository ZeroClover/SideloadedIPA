## Context

The repository has completed the multi-bundle signing migration and now executes production work through the package-owned staged pipeline. Its safety-critical boundaries are strong: source selection is unambiguous, bundle inventory and signing plans are typed, cache hits are reverified, and publication is atomic and fail-closed. The `simplify-ci-workflows` change has also reduced CI to a consolidated PR validation job and the production signing schedule, but that completed change is still active in OpenSpec and must be archived before this proposal is applied.

The remaining work is cross-cutting rather than a new product feature. `ProductionPipeline` still owns source resolution through publication in one module and re-inventories the same source across stage commands. Direct URLs are mutable and unpinned. The downloader does not enforce HTTPS, a maximum transfer size, or the GitHub-advertised asset size. The web data reader relies on implicit Next.js fetch behavior and unchecked type assertions. Local and CI Python selection can drift even though the lockfile and static-analysis targets assume Python 3.11. Several configuration fields have exactly one accepted value, and migration-era qualification and documentation surfaces remain after their CI callers were removed.

The design must reduce those surfaces without weakening independent signed-output verification, Apple mutation boundaries, evidence retention, cache correctness, or publication rollback. Public object keys and supported CLI commands must remain stable.

## Goals / Non-Goals

**Goals:**

- Make every network-delivered IPA and registry document bounded, authenticated where applicable, schema/integrity checked, and fail-closed.
- Make local and CI dependency resolution reproducible and keep reviewed dependencies current without adding another standalone scheduled workflow.
- Make one canonical source and inventory manifest authoritative for downstream stages in a run while preserving independent verification of signed output.
- Reduce the production orchestrator to coordination and move cohesive stage transactions into package-owned modules.
- Remove configuration and qualification surfaces that no longer represent a supported choice or independent implementation.
- Remove stale operational guidance and hand-maintained duplicate instructions after proving that no supported consumer is lost.

**Non-Goals:**

- Change Apple identifier, entitlement, profile, signing, signature-verification, R2 object-key, registry transaction, or rollback semantics.
- Replace the existing monorepo or `src/` package layout, introduce services, a dependency-injection framework, or generic stage inheritance.
- Restore the standalone scheduled integration workflow, private canary modes, or migration-only production workflow inputs removed by `simplify-ci-workflows`.
- Change the public CLI command names or require a new runtime schema-validation dependency for the small web registry document.
- Move every test solely for directory aesthetics or lower the 95 percent package coverage gate.

## Decisions

1. **Sequence this change after `simplify-ci-workflows` is archived.** The implemented CI simplification remains the baseline. This change may update its consolidated validation job, but it will not carry a competing delta for that still-active capability. Applying both changes concurrently was rejected because both touch workflow and documentation contracts and would make archive order significant.

2. **Use one internal source-download policy instead of more operator configuration.** The downloader will receive a typed policy containing a reviewed maximum byte count, timeout values, chunk size, and bounded retry count. Production composition supplies package defaults; tests may inject smaller limits. `Content-Length`, when present, is checked before streaming, every chunk advances the same hard limit, GitHub's advertised size is compared with actual bytes, and an available advertised SHA-256 is mandatory evidence. Adding per-task transfer knobs was rejected because it expands configuration for an operational safety invariant.

3. **Treat direct URLs as reviewed immutable sources.** A direct `ipa_url` requires a canonical 64-character `ipa_sha256`. URL and digest form the source identity and cache fingerprint. Changing either invalidates the task; unchanged identity may reuse a cache only through the existing prerequisite and full-artifact verification gates. Continuing to rebuild a mutable URL on every run was rejected because it is neither reproducible nor proof that reviewed bytes were signed.

4. **Make the web registry contract explicit without adding a schema library.** A small package-local TypeScript decoder will validate the object shape, unique slug, non-empty identity fields, and HTTPS IPA/icon URLs before returning `AppEntry[]`. Server fetches will explicitly opt into the Next.js Data Cache with the `apps` tag. The authenticated revalidation route retains `revalidateTag("apps", "max")`, so a valid cached registry remains available during background refresh. Missing production configuration, an invalid first response, or a cache miss with an origin failure fails the request/build; it never synthesizes an empty registry. Fixtures require an explicit validation/development data mode and cannot be enabled in the deployed production environment. Adding Zod or another runtime dependency for this single document was rejected.

5. **Pin supported runtimes and let reviewed automation update them.** The repository will record one production Python patch/minor contract, Node.js 22 for the current web application, and an exact uv version used to interpret `uv.lock`. CI installs those versions explicitly and uses frozen/locked commands. The implementation selects the then-current supported Python patch only after the complete dependency suite passes; the minimum `requires-python` contract remains separately checked. Dependabot will manage the supported `uv`, `npm`, and `github-actions` ecosystems, while PR validation runs `uv audit --frozen` and npm audit against committed locks. A high/critical advisory may be suppressed only by an advisory-specific record containing reachability analysis, owner, remediation condition, and expiry; blanket or permanent ignores are rejected.

6. **Keep one orchestrator and extract concrete stage transactions.** `ProductionPipeline` remains the compatibility facade and coordinator, but source/inventory, Apple sync, signing, verification, publication, and report/cache persistence move into cohesive functions or services under `pipeline/stages/`. Existing typed dependencies are passed directly; no abstract stage base class or service container is introduced. The orchestrator invokes the same public operations and preserves error codes and side-effect journaling. A line-count target is guidance, not an acceptance gate; the real gate is that business decisions and duplicated signing/report-writing paths leave the coordinator.

7. **Persist source and inventory evidence once per run.** Inspect writes schema-versioned source-selection, downloaded-source, and inventory manifests atomically. A downstream command loads them by run ID and task, verifies schema, task identity, source URL/digest, file digest/size, and predecessor success, then constructs its typed input without re-resolving or re-inventorying the unsigned IPA. Missing or mismatched evidence fails closed. This reuse ends at the trust boundary: post-signing verification still reopens and inventories the output IPA independently and cache hits still pass the full verifier.

8. **Turn single-choice signing options into invariants.** Preserve-source-suffix mapping, unknown-profile-bundle rejection, and iOS development profile type remain the supported behavior but are no longer user fields. Configuration containing `id_strategy`, `unknown_profile_bundles`, or `profile_type` receives a migration diagnostic; production, example, and fixture configuration are migrated in the same change. Keeping deprecated keys indefinitely was rejected because it preserves nearly all parser, model, test, and documentation surface without offering compatibility value in this repository-owned application.

9. **Keep a minimal, repeatable backend qualification lifecycle.** A backend version, executable digest, patch set, invocation contract, or per-bundle entitlement behavior change triggers requalification. PR validation retains the deterministic multi-bundle fixture against the real patched binary. A single documented operator entry point coordinates the optional macOS codesign oracle and stores redacted comparison evidence. Any Apple resource preparation reuses production inspect/plan/sync primitives; independent qualification mutation/reset code is removed after consumer and parity checks. Retaining every migration utility was rejected because the production pipeline is now the authority; deleting the real fixture or oracle was rejected because backend correctness still needs independent evidence.

10. **Prefer deletion and canonical current documentation over an in-repository museum.** Current configuration, architecture, operator, security, and troubleshooting documentation remain. Historical plans and obsolete examples are deleted after enduring decisions are extracted; Git history is the archive unless an external link requires a short redirect document. `.codex` is the canonical repository-local instruction source for this workspace. Other client mirrors are removed when unsupported, or regenerated by the existing OpenSpec tooling and equality-checked when their consumer is explicitly retained. A new bespoke generator solely to save duplicated lines is rejected.

11. **Verify behavior before and after each extraction.** Characterization tests are added only where an existing safety or compatibility boundary lacks coverage. Stage extraction proceeds one transaction at a time, with tests and type checks after each section. The default local test command no longer emits HTML coverage; CI invokes the explicit terminal coverage gate, and HTML remains an opt-in diagnostic command. Moving test files or adding abstraction-only tests is rejected.

## Risks / Trade-offs

- [Direct-URL and signing configuration changes reject existing external files] → ship precise field-level migration errors, update all repository configuration in the same section, and document the required checksum command before merging.
- [A download limit rejects a legitimate future IPA] → select the default from observed production assets with headroom, report declared/actual/limit bytes, and change the reviewed package policy rather than silently bypassing it.
- [Manifest reuse could consume stale or tampered unsigned input] → bind every manifest to run, task, schema, URL, size, and digest; reload the file digest before use; keep signed-output inventory and verification independent.
- [Stale-while-revalidate can briefly serve the previous app registry] → this is preferable to an empty/broken download page; immutable artifact URLs and atomic registry publication keep the previous entry valid until refresh succeeds.
- [Pinned runtime versions can age] → Dependabot update PRs, lock audits, and the full acceptance stack make version movement reviewed and routine.
- [Splitting the orchestrator creates broad mechanical churn] → preserve its facade and extract one stage at a time without changing report schemas or public commands.
- [Removing qualification or instruction files can surprise an unrecorded consumer] → require repository caller/link inventory and a documented consumer decision before deletion; retain Git recovery and parity evidence.
- [Security audit findings can be temporarily unfixable upstream] → allow only advisory-specific, owned, expiring exceptions and automatically fail again when a fixed version becomes available.

## Migration Plan

1. Archive the completed `simplify-ci-workflows` change and establish a clean baseline with Python, web, workflow, and OpenSpec validation.
2. Pin Python/Node/uv contracts, remove the unreachable pre-3.11 dependency, update vulnerable locks, add Dependabot ecosystems and explicit audit gates, and separate the opt-in HTML coverage report.
3. Add download policy and integrity tests; introduce `ipa_sha256`; migrate direct-source examples; then switch direct-source cache identity and reject insecure/missing-digest configurations.
4. Add the web registry decoder and failure tests, opt into tagged Data Cache explicitly, require an explicit fixture mode in CI/local validation, and verify authenticated revalidation plus ITMS output.
5. Add atomic source/inventory manifest loading, prove tamper/mismatch failure, and then extract source, Apple, signing, verification, publication, and reporting transactions while preserving the facade.
6. Remove the three single-choice configuration fields, consolidate backend qualification, and delete duplicate production decisions and unsupported wrappers after parity/caller checks.
7. Rewrite current documentation, delete stale plans/insecure examples, decide supported agent clients, and remove or reproducibly synchronize duplicate instructions.
8. Run the full acceptance stack, compare public CLI/report/object/config migration behavior, and perform one non-publishing local production composition plus the patched-zsign integration contract before acceptance.

Rollback is a normal Git revert for code, workflow, documentation, and lock changes. Because published object and registry schemas do not change, no R2 rollback migration is required. If a rollback occurs after configuration migration, restore the old parser and the old configuration files together; do not reintroduce mutable direct URLs without their prior reviewed values.

## Open Questions

None. Exact runtime patch versions and the source byte ceiling are selected during implementation from then-current supported releases and measured production assets, using the requirements above as acceptance constraints rather than encoding values that can become stale in this planning artifact.
