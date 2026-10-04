# Design

## Context

See proposal.md. Production publishes verified cache hits as well as rebuilt artifacts, so scheduled runs already reach revalidation and cleanup without new releases. Existing gateway boundaries and atomic rollback remain authoritative.

## Goals / Non-Goals

**Goals:** bounded-memory verification, durable retirement, safe retries, explicit failures.

**Non-Goals:** replacing the architecture, batch policy, Apple provisioning, cache fingerprints, qualified zsign, or CI orchestration.

## Decisions

- Keep content-addressed filenames rather than UUIDs: full SHA-256 distinguishes changed signed bytes while retries and unchanged verified cache hits keep the same key.
- Store retirement timestamps in `<registry-key>.gc.json`. Scan only managed IPA/icon shapes within selected slugs; continue checking pending retirements on later runs. Current references clear marks. Before writing a registry, reset retirement marks for its referenced keys, including when an old content-addressed object is revived and promotion subsequently rolls back. Protect previous references during promotion to prevent stale marks prematurely deleting just-retired artifacts. Start retention at first observed non-reference, never upload age.
- Persist pending timestamps before deletion. Batch deletes and check per-object errors; failed work remains retryable. Invalid state aborts cleanup. Never clean after failed revalidation.
- Keep pre-promotion compensation. After an attempted registry write, rollback cannot retract manifests already seen by clients: retain these uploads for the grace sweep and attempt cache expiry for the restored snapshot. Failed rollback preserves potentially referenced artifacts.
- Retain the existing secret header over TLS, require POST and immediate tag expiry. R2 JSON uses no-store. Next keeps tagged caching with 60-second refresh and 10-second origin timeout. Dependency failures return redacted manifest 503. Explicit expiry favors fresh-or-error over stale installation URLs; ordinary time-based revalidation remains framework-managed. Reject XML-invalid text, URL fragments and dot slugs.

## Risks / Trade-offs

- Single publisher remains mandatory: keep workflow concurrency, prohibit concurrent local publishers.
- Cleanup needs a later successful publication; outages conservatively extend retention.
- Links held longer than the grace period can expire; this is not permanent archival.
- CDN overrides can defeat no-store: bypass JSON caching and purge existing registry cache.

## Migration Plan

Deploy POST web and publisher changes together. Existing referenced artifacts remain valid; unreferenced managed objects get a fresh grace period, with an automatically created sidecar. Do not roll back to immediate deletion. No legacy scripts or backfill required.
