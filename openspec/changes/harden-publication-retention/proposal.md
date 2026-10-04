# Proposal

## Why

Upstream already supersedes the legacy signing rewrite. Publication still immediately removes superseded objects and rolled-back uploads, while web caches can serve stale manifests. Port remaining fixes without resurrecting deleted scripts or weakening multi-bundle verification.

## What Changes

- Keep content-addressed retries, use complete IPA SHA-256 identities, and stream remote digest verification.
- Persist first-unreferenced timestamps and retain managed IPA/icon objects for at least 48 hours, protecting current and immediately preceding registry references.
- Retain uploads potentially advertised during registry rollback; compensate immediately only before promotion.
- Check partial delete failures, batch deletes, close response bodies, and fail closed on storage errors.
- **BREAKING**: authenticated POST-only immediate cache expiry replaces GET/SWR; bound origin reads and return explicit manifest 503 responses.
- Update vulnerable web dependencies within Next 16 and remove the obsolete PostCSS override and expired sharp exception; preserve the existing audit gate.

## Capabilities

### New Capabilities

### Modified Capabilities

- signing-workflow-orchestration: durable retirement grace and safe rollback compensation.
- download-registry-delivery: immediate POST revalidation, bounded reads and unavailable manifests.

## Impact

Use existing adapters/services and web modules with tests and operator guidance. Preserve batch policy, cache verification, pinned tools, multi-bundle signing and CI. No production effects during validation. Legacy work stays on fix/legacy-signing-retention.
