## Why

The production signing path is already fail-closed and well tested, but several repository boundaries still depend on implicit runtime behavior: the download site does not make its cache contract explicit, remote IPA downloads have no transport or byte limit, and CI does not pin the Python/uv toolchain that interprets the lockfile. At the same time, the main production orchestrator, single-choice configuration fields, migration-era qualification utilities, duplicated agent instructions, and historical documents retain substantially more surface than the supported operation requires.

## What Changes

- Make the R2 `apps.json` registry a schema-validated, explicitly cached source shared by the download page and ITMS manifest route; fail closed on production misconfiguration or invalid registry data instead of silently substituting fixtures or an empty registry.
- Require HTTPS and bounded downloads for remote IPA sources, verify GitHub-advertised size and available digest evidence, and use bounded retries only for an unchanged resolved asset identity.
- **BREAKING** Require direct `ipa_url` tasks to declare a reviewed `ipa_sha256`; use that digest as the immutable source identity instead of rebuilding an unversioned mutable URL on every run.
- Pin the repository's Python, Node.js, and uv toolchain contracts, keep frozen lockfile installation, and add reviewed dependency-audit/update gates without accepting an unbounded security exception.
- Split the production pipeline into cohesive stage modules while keeping one thin package-owned orchestrator, the existing CLI surface, canonical reports, independent output verification, and atomic publication semantics.
- Persist the canonical source and inventory manifests once per run and make downstream stages validate and consume them instead of repeatedly resolving and inventorying the same source IPA.
- Reuse one normalized Apple profile snapshot across a synchronization transaction, while continuing to download and validate each selected profile independently and refreshing remote state after profile mutation or an uncertain create outcome.
- **BREAKING** Remove the single-choice `id_strategy`, `unknown_profile_bundles`, and `profile_type` fields from task configuration; preserve-source-suffix mapping, fail-closed unknown-bundle handling, and iOS development profiles become internal invariants.
- Consolidate backend qualification behind one documented entry point that reuses production planning/synchronization primitives; retain the deterministic patched-zsign PR contract and operator-run macOS oracle while removing migration-only mutation/reset paths and duplicate wrappers after caller checks.
- Remove or archive stale operational plans and insecure examples, shorten the README to supported entry points, and establish one canonical source for duplicated agent/OpenSpec instructions.
- Preserve the reduced CI surface introduced by `simplify-ci-workflows`; this change does not restore the removed standalone scheduled integration workflow or auxiliary production modes.

## Capabilities

### New Capabilities

- `download-registry-delivery`: Defines validated R2 registry reads, explicit Next.js cache/revalidation behavior, production fixture boundaries, and safe generation of download/ITMS responses.
- `toolchain-reproducibility`: Defines pinned local/CI runtimes, frozen dependency installation, dependency auditing, and time-bounded handling of upstream security exceptions.

### Modified Capabilities

- `github-release-tracking`: Adds HTTPS-only asset transport, download byte limits, advertised-size and digest verification, and identity-preserving retry behavior.
- `task-configuration`: Requires an immutable SHA-256 identity for direct IPA URLs and rejects insecure remote source URLs.
- `workflow-optimization`: Replaces unconditional direct-URL rebuilds with digest-based cache identity and invalidation.
- `signing-task-configuration`: Removes configuration fields that expose no supported choice and makes their safe values internal signing invariants.
- `signing-workflow-orchestration`: Requires downstream stages to consume validated canonical source/inventory manifests from the same run while retaining independent final-artifact verification.
- `multi-bundle-signing`: Defines the supported backend requalification trigger, minimum retained evidence, and one production-primitive-based qualification entry point.

## Impact

- Python changes primarily affect `src/sideloadedipa/pipeline/`, `sources/`, `config/`, `domain/`, `tools/`, Apple state/profile adapters, related tests, and `configs/*.toml`.
- Web changes affect `web/lib/apps.ts`, route tests, environment documentation, and the production build contract.
- Toolchain changes affect `.python-version`, the web Node version contract, `pyproject.toml`, lockfiles, and the consolidated PR validation workflow after `simplify-ci-workflows` is archived.
- Documentation cleanup affects README, configuration/operator/security documentation, historical plans, and duplicated repository-local agent instructions.
- Direct-URL configuration and the three removed single-choice signing fields require an explicit same-change migration of examples, fixtures, and any external operator configuration.
- Public artifact keys, CLI command names, run-report schemas, Apple resource semantics, signing cache safety, independent verification, R2 registry atomicity, and publication rollback behavior remain compatible.
