# Implementation baseline

Recorded on 2026-07-21 before changing legacy behavior.

## Repository

- Planning baseline: `9e04744`.
- Implementation baseline: `cca50da51b97dae2f4edf391025d629e0cc87705`.
- `origin/master` after fetch: `cca50da51b97dae2f4edf391025d629e0cc87705`.
- The only successor to the planning baseline is the reviewed OpenSpec planning commit; no code integration was required.

## Upstream fixtures and tools

- LiveContainer latest stable release: tag `3.8.0`, commit `e370a92dfc03ce109ebce00ed4a7cfc64ad1c801`.
- `LiveContainer.ipa`: 4,707,271 bytes, SHA-256 `b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6`.
- `LiveContainer+SideStore.ipa`: 35,403,538 bytes, SHA-256 `97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425`.
- Both assets were downloaded from the GitHub release and independently hashed during implementation; the bytes match GitHub's release metadata and the reviewed planning baseline.
- Latest stable zsign: `v1.1.1`; Linux musl archive SHA-256 `9880b0e1290dea211481fd031bcca8d0d7f3f09ba1c6a89743b3422df1ac14b9`.
- Latest stable App Store Connect CLI: `3.1.1`; Linux amd64 executable SHA-256 `57cca59153eda109faf18d72c8bb0989ed0ee6e0a3082ce73ffa08174afbf2fd`.

## Pre-refactor quality baseline

- `uv sync --frozen`: passed.
- `pytest` with configured coverage: 191 passed; aggregate legacy `scripts` coverage 54%.
- strict `mypy scripts/`: failed with 59 pre-existing errors in `sync_profiles_asc.py`, `check_changes.py`, and `run_signing.py`. The PR workflow already marks this check non-blocking and identifies the debt as pre-existing.
- `black --check scripts/`: passed.
- `isort --check-only scripts/`: passed.
- Extending the Black baseline to tests reports three pre-existing unformatted files: `test_apps_registry.py`, `test_check_changes.py`, and `test_run_signing.py`.
- Web plist golden tests: passed.
- Next.js production build: passed with Next.js 16.2.10.
- GitHub Actions YAML parsing and checksum-verified actionlint 1.7.12: passed.
- `npm audit`: two moderate findings from PostCSS through Next.js; no high or critical finding. This is pre-existing dependency state and must be rechecked during toolchain migration.
- `git diff --check`: passed.
- The complete 206-test suite, including icon extraction/publication and R2 registry/cleanup characterization, was rerun against the unchanged implementation baseline after the fixture locks were added; all tests passed and aggregate legacy coverage increased to 67%.

## Normative ownership

The archived `add-ci-caching-optimization` delta and the installed `openspec/specs/github-release-tracking/spec.md` both require first-match selection plus a warning when multiple assets match. The only delta changing this behavior is this change's `specs/github-release-tracking/spec.md`, which requires exactly one match. Characterization tests preserve the legacy behavior until task 4.7 changes it.

## Production release audit

The latest stable release of each active production task was queried through the GitHub API. Every task still has exactly one `*.ipa` match; the recorded candidates are in `tests/fixtures/baseline/production-release-audit.json`. No explicit selector is required before the fail-closed migration for the current release snapshot.

## Compatibility fixture

Current environment inputs, exit codes, GitHub Actions outputs, and secret-redaction expectations are recorded in `tests/fixtures/baseline/compatibility-contract.json`. Characterization tests enforce the executable portions of this contract.
