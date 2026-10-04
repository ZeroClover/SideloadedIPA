# Verification

Validated against upstream e6bff52 on 2026-10-03, without production Apple/R2 calls or deployment.

## Automated checks

- Python 3.11.15 and 3.12.12: each 887 passed, 3 skipped; 95% coverage with the existing 95% gate. Independent coverage files were used for final runs.
- Strict mypy: 113 source files passed. Black/isort and git diff whitespace checks passed.
- Web: 37 tests and byte-identical plist golden fixtures passed; TypeScript and Next 16.3.8 production build passed with explicit fixture data.
- npm audit: zero vulnerabilities. Existing dependency gate passed with zero exceptions; no severity or expiry checks were weakened.
- OpenSpec strict validation passed. Existing offline zizmor high-severity gate passed with its pre-existing suppressions.

## Local production-server smoke check

A loopback-only Next production server used a disposable test secret and an intentionally unavailable loopback registry origin. Verified GET refresh 405, unauthenticated POST 401, authenticated POST 200, and dependency-failed manifest 503 with no-store. Server stopped after verification.

## Important regression scenarios

- Full SHA-256 identities distinguish same-version re-signs and retain retry identity.
- Remote IPA digest verification uses 1 MiB reads and closes streams on success/failure.
- First-unreferenced timestamps survive process restart, wait 48 hours, reset on renewed references, and recover from partial deletes or state-write failures.
- Current and previous registry references survive promotion; temporarily advertised rollback artifacts are retained.
- Reviving an already-retired historical object clears its old mark before registry write, even if promotion later rolls back.
- Managed-key scoping, pagination, 1000-object delete batches, safe URL round-trips, and storage-error fail-closed behavior are covered.
- Fresh schema/XML validation, timeout configuration, POST-only immediate tag expiry and manifest 503 behavior are covered.

## Baseline findings and limits

The upstream baseline had two clock-sensitive cache tests failing against fixed-date profile fixtures. Injecting the fixture clock fixes the tests without relaxing production cache validation. The expired sharp exception and newly vulnerable web lockfile were replaced by patched dependencies, not renewed exceptions.

Three opt-in tests remain skipped: one requires the qualified patched zsign binary and two download pinned LiveContainer IPAs. No physical-device acceptance, real R2 deletion, Apple mutation or production deployment was performed.

Keep the single-publisher constraint. Deploy the POST endpoint and publisher transport together, bypass CDN caching for registry/GC JSON, and purge previously cached registry JSON as described in docs/operator-runbook.md.
