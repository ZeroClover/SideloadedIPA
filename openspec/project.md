# Project Context

## Purpose

SideloadedIPA automates acquisition, Apple development signing, verification, and serverless distribution of selected iOS IPA releases. GitHub Actions synchronizes Apple provisioning resources, signs selected artifacts, publishes immutable IPA/icon objects and the `site/apps.json` registry to Cloudflare R2, and revalidates a Vercel-hosted installation site.

## Tech Stack

- Python 3.11+, TOML configuration, `uv`/`uv.lock`, Hatchling, boto3, and Pillow
- `zsign` on `ubuntu-latest` for IPA signing and App Store Connect CLI (`asc`) for Apple resource access
- GitHub Actions for scheduled, manual, webhook, PR, cache, and production workflows
- Cloudflare R2 as S3-compatible artifact/registry storage and Vercel revalidation for publication
- Next.js 16, React 19, TypeScript 5.7, Node.js 22, and npm for the download/plist web application
- pytest/pytest-cov, strict mypy, Black (100 columns), isort, and OpenSpec validation

## Project Conventions

### Code Style

- Use Python 3.11 type syntax and keep new/changed Python modules clean under strict mypy; legacy strict-mypy debt is currently reported but non-blocking in PR CI.
- Format Python with Black at 100 columns and isort's Black profile. Prefer `pathlib`, explicit typed values, context-managed resources, and argv-list subprocess calls over shell composition.
- Keep business rules deterministic and independently testable. External APIs, filesystems, subprocesses, clocks, credentials, R2, and publication are side-effect boundaries.
- Use TOML for task configuration and JSON for machine-readable cache, registry, manifest, and diagnostic state. Stable public identifiers such as task slugs and R2 object paths require backwards-compatible migration.
- Never print secrets or raw private signing material. Diagnostics must be actionable, structured where practical, and redacted before logs or retained artifacts.

### Architecture Patterns

- The production Python execution path is the package-owned manifest orchestrator. It performs source inventory, Apple plan/apply, signing, independent verification, cache selection, and publication; remaining script entry points are compatibility wrappers for supported operational tools only.
- `configs/tasks.toml` is the production task source. A task uses exactly one of `ipa_url` or `repo_url`; GitHub sources use `release_glob` (default `*.ipa`) and optional prerelease selection.
- `site/apps.json` in R2 is the publication source of truth. IPA and icon objects use immutable/versioned or content-addressed keys; registry mutation and Vercel revalidation expose them to the web app.
- OpenSpec baseline requirements under `openspec/specs/` are normative. Active changes must modify an existing requirement through a delta spec instead of adding a conflicting requirement under another capability.
- Refactors must preserve working entry points and observable behavior behind compatibility layers until characterization and production-parity evidence allow removal.

### Testing Strategy

- Run Python tests with `uv run pytest`; use focused unit tests plus fixtures at I/O boundaries and preserve coverage of the existing `scripts` package during migration.
- Run `uv run black --check scripts/`, `uv run isort --check-only scripts/`, and `uv run mypy scripts/`; new package paths must be added to these gates as they are introduced.
- Validate production TOML parsing without credentials. Live Apple mutations, signing secrets, and R2 publication are never prerequisites for ordinary PR tests.
- For the web app, run `npm ci`, `npm test`, and `npm run build` from `web/`.
- For planning changes, run `openspec validate <change> --strict` and `git diff --check`. Characterize current behavior before refactoring, test failure paths and forbidden side effects, and use independent signing/profile evidence for security-critical acceptance.

### Git Workflow

- `master` is the current integration branch; PR checks also recognize `main`. Use short-lived topic branches and reviewed pull requests for production changes.
- Repository history uses concise Conventional Commit-style subjects such as `feat(scope):`, `fix(scope):`, `refactor:`, `ci:`, and `chore:`.
- Keep unrelated user changes intact. Do not commit `work/`, extracted IPAs, profiles, keys, certificates, local environment files, or generated secret-bearing diagnostics.
- Implement OpenSpec work section by section, record acceptance evidence, and archive a change only after its tasks and production acceptance are complete.

## Domain Context

- An IPA can contain a root `.app`, nested `.app`/`.appex` bundles that each require an explicit App ID and provisioning profile, and profile-free nested frameworks/dylibs that still require valid signatures.
- Bundle identifiers, App Identifier Prefix/team values, capabilities, provisioning-profile authorization, and executable entitlements must agree. A successful signing-tool exit is not sufficient proof of a usable artifact.
- Nested code is signed from the deepest component outward and the containing app last. Repackaging must preserve the planned executable graph and reject unplanned signable content.
- GitHub releases can contain zero, one, or multiple IPA assets. Source selection is security- and reproducibility-relevant and must follow the active `github-release-tracking` capability contract.
- Device changes, certificates, capability settings, App IDs, profiles, source releases, signing policy, and tool versions can all invalidate a previous signing result or cache entry.
- Some Apple operations are safe, additive, and idempotent; others require Account Holder/Admin action or Apple approval. Planning must distinguish automation from human prerequisites and must not use undocumented APIs.

## Important Constraints

- Production currently runs on Linux. A move to macOS requires correctness evidence plus an explicit cost/runtime decision.
- Preserve existing single-bundle tasks, public download URLs, task slugs, registry semantics, and last-known-good published artifacts during migration.
- Fail closed on ambiguous source selection, missing/mismatched profiles, unauthorized or lost functional entitlements, invalid nested signatures, unsafe archives, and unplanned bundles.
- Apple mutations performed by CI are additive only. Do not automatically delete Bundle IDs/profiles, disable capabilities, remove App Group associations, or revoke certificates.
- Credentials and private material (`APPLE_DEV_CERT_*`, `ASC_*`, `R2_*`, GitHub/Vercel tokens, P12/P8 data, and private profiles) must come from approved secret stores and must never be committed, publicly cached, or logged.
- Use official documented Apple/GitHub APIs and checksum-verified pinned external binaries. Re-check upstream versions and API assumptions at implementation time.

## External Dependencies

- GitHub Releases/API for source discovery, asset download, and workflow tokens
- Apple Developer Program, App Store Connect API/CLI, Developer Portal-only capability/App Group operations, registered devices, certificates, App IDs, and provisioning profiles
- [`zsign`](https://github.com/zhlynn/zsign) as the current cross-platform signing backend; exact multi-bundle entitlement behavior requires qualification
- [App Store Connect CLI](https://github.com/rorkai/App-Store-Connect-CLI) as the current Apple API adapter dependency
- Cloudflare R2 for immutable objects and the application registry
- Vercel/Next.js for the download page, dynamic plist routes, analytics, and on-demand revalidation
