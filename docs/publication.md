# Publication, retention and recovery

Read before changing R2 publication, registry delivery, cache revalidation or recovery.
Sources: src/sideloadedipa/pipeline/publication_service.py, pipeline/stages/publication.py,
adapters/publication/r2_store.py, web/lib/apps.ts, web/lib/revalidation.ts.

## Publication contract

Publish only independently verified bytes with approved task publication enabled.
IPA keys contain full signed-content SHA-256; a same-version re-sign changes URL,
while unchanged verified cache hits keep it. Icons use managed immutable identity.
The default catalog key is site/apps.json; configuration can override it.

The sequence is upload immutable objects → write catalog → revalidate web cache →
progress retirement. Upload or verification failure before catalog mutation leaves
the previous catalog intact. Later failure can require compensating rollback: do
not describe the entire R2/web operation as an indivisible distributed transaction.

Keep Actions concurrency enabled and only one publisher active. The current system
has no general multi-writer conditional registry update. Local publication alongside
Actions can overwrite another writer's catalog or invalidate rollback assumptions.

## Retention and deletion boundary

- Protect both current and immediately preceding registry references after success.
- Record unreferenced managed IPA/icon keys in <registry-key>.gc.json.
- Delete only after 48 hours from the **first observation as unreferenced**, not age
  of upload. Persist retirement timestamps before deletion.
- Scope cleanup to selected task slugs and recorded retirements; do not sweep manual
  namespaces or unrelated objects. Partial deletion errors remain retryable.
- Missing sidecar restarts grace conservatively; corrupt/inaccessible state stops
  cleanup. No cleanup after failed revalidation.
- Verified cache-hit publications still refresh cache and progress retirement.
- Once a catalog write was attempted, potentially advertised uploads remain for
  retirement even after rollback. Never immediately delete URLs clients may hold.

Do not clear retirement state or revert to immediate deletion during recovery.
Regression anchors: tests/test_publication_retention.py, tests/test_r2_retention.py,
tests/test_publication.py, tests/test_pipeline_failure_injection.py.

## Coordinated web delivery

POST /api/revalidate requires X-Revalidate-Secret matching web REVALIDATE_SECRET
(publisher VERCEL_REVALIDATE_SECRET). GET returns 405; unauthorized requests return
non-cacheable 401. Authenticated POST expires the apps tag immediately using
{ expire: 0 }, not stale-while-revalidate. Deploy publisher and route together.

R2 catalog and GC JSON use Cache-Control: no-store. Exempt these paths from CDN cache
overrides and purge old cached catalog once during an authorized rollout. Next
owns the tagged catalog cache: 60-second refresh interval, 10-second origin timeout.
Initial dependency failure returns non-cacheable 503, not an empty catalog or a
false unknown-slug 404. Immutable artifact/image caching is unchanged.

Regression anchors: web/test/routes.test.ts, web/test/delivery-retention.test.ts,
web/test/apps.test.ts. Environment names are in [Environment](environment.md).

## Recovery

Inspect the redacted stage/run report and retained registry/object identities before
retrying. If catalog rollback or cache expiry failed, keep potentially advertised
objects and retry the authorized transaction; do not manually delete them.

Reverting task configuration plus force_rebuild is **not** a universal version
rollback: a GitHub source still selects its configured current release. Prepare a
reviewed source pinned to the intended older bytes or a verified retained artifact,
then use the normal verification/publication gate with explicit authorization.
Do not bypass source provenance, device/profile validity, or mutate the catalog by
hand as a shortcut. Coordinate rollback with retention and web cache state.
