# Architecture

SideloadedIPA is a staged transaction pipeline with two trust boundaries: an
unsigned source is selected and inventoried once for a run, while every signed or
cached output is independently reopened and verified before it can be promoted or
published.

## Production stages

`sideloadedipa` exposes `inspect`, `plan`, `sync`, `sign`, `verify`, `publish`, and
the convenience composition `run`.

1. **Source and inventory** resolves one source identity, downloads it under a
   bounded policy, validates the ZIP, and discovers the complete signable graph.
2. **Apple** derives exact App IDs, capabilities, App Groups, and development
   profile requirements. `plan` is read-only; `sync --apply` makes only additive,
   idempotent changes.
3. **Signing** maps every profile-bearing bundle to one policy/profile, calculates
   the complete signing fingerprint, validates any cache candidate, and invokes
   the qualified backend in deepest-first/root-last order.
4. **Verification** independently inventories the signed IPA and checks graph
   parity, embedded profiles, XML/DER entitlements, identifiers, certificate/team
   identity, package integrity, and every nested signature.
5. **Publication** verifies again, uploads immutable objects, promotes the registry
   atomically, revalidates the web cache, and only then removes unreferenced stale
   objects.

`src/sideloadedipa/pipeline/production.py` is the compatibility facade and ordered
coordinator. Concrete transactions live under `pipeline/stages/`; domain values,
ports, and adapters remain explicit dependencies rather than a service container
or generic stage framework.

## Canonical evidence chain

Every visible stage writes a schema-versioned canonical manifest beneath
`work/pipeline/<run-id>/`. Source and inventory manifests bind:

- schema version, run ID, task, and predecessor success;
- source kind, immutable URL/release asset identity, expected and actual size,
  and expected/measured SHA-256;
- downloaded file size/digest and the canonical bundle-graph digest.

Downstream stages reload those typed documents and verify their canonical digest,
identity, predecessor, file metadata, and source bytes. A missing, truncated,
cross-run, cross-task, unsupported, failed, or tampered manifest stops the chain.
Atomic writes ensure an interrupted update cannot look complete.

This reuse deliberately ends at signed output. Verification and cache-hit
publication reopen the IPA and rebuild its graph independently; unsigned-source
evidence cannot authorize signed bytes.

## Source trust boundary

GitHub release sources bind the resolved release tag and asset ID, require one
selector match, compare advertised and actual sizes, validate an advertised digest
when present, and always retain the measured SHA-256. Direct URL sources require
HTTPS and a reviewed configured SHA-256.

The downloader uses a package-owned maximum size with bounded timeouts, chunks,
and attempts. Redirect downgrade, declared-length overflow, streamed overflow,
identity drift between retries, digest mismatch, and exhausted transport failure
have distinct typed diagnostics. Failed attempts never leave a readable source
artifact.

## Signing policy and backend

The unsigned inventory is the authority for bundle coverage. Target identifiers
preserve the source suffix beneath the configured root unless a rule gives an
explicit target. Any uncovered profile-bearing bundle fails closed. All generated
profiles are iOS development profiles.

App Group aliases and entitlement templates are repository-controlled. Final
entitlements must be authorized by each mapped profile; a declared entitlement
drop also requires a rationale. Backend output is not trusted as verification
evidence merely because the signing subprocess succeeded.

`ZsignBackend` accepts only the reviewed zsign version, executable digest, patch
contract, and per-bundle invocation shape. The operator command
`sideloadedipa-qualify-backend` binds deterministic fixture, backend, plan, output,
macOS oracle, and comparison documents into one redacted evidence file. A missing
oracle is `manual-gate-unmet`, never success.

## Cache model

Signing artifacts are addressed by a fingerprint covering source identity and
digest, bundle graph, signing policy, profile/certificate state, tool identity,
and relevant publication inputs. `work/cache/signing-index.json` and stage/cache
decisions use canonical atomic persistence.

A matching fingerprint is only a candidate. Production rechecks current profile
and certificate prerequisites, validates the stored verification-report digest,
and runs the full signed-artifact verifier. Drift or tampering produces a
`cache-rejected` decision and a rebuild. Successful non-publishing verification
may promote cache evidence immediately; publishing verification waits for the
publication transaction to succeed.

## Publication transaction

R2 object keys are versioned and immutable. A batch proceeds in this order:

1. independently verify every candidate;
2. upload new IPA and icon objects;
3. atomically write the validated `site/apps.json` registry;
4. call the authenticated Vercel revalidation endpoint;
5. delete only unreferenced stale objects for affected slugs.

Failure before registry promotion leaves the prior registry authoritative.
Compensation removes only newly uploaded, unreferenced objects; it never deletes a
previously referenced artifact. Failure after promotion is reported with enough
redacted evidence for explicit operator recovery.

## Web registry boundary

The Next.js application reads the R2 registry in explicit `origin` mode or a
bundled registry in explicit `fixture` mode. The dependency-free decoder validates
the complete document, unique slugs, non-empty identities, and HTTPS artifact
URLs before returning typed entries.

Origin reads opt into the persistent Next.js Data Cache with the `apps` tag.
Header-authenticated revalidation calls `revalidateTag("apps", "max")`, allowing a
previous valid value to remain available during a background refresh failure. An
initial origin, transport, JSON, or schema failure is surfaced; the application
does not replace it with an empty catalog. Production deployment rejects fixture
mode.

The page and `/apps/<slug>/itms.plist` route consume the same validated entry.
Plists escape XML values, use HTTPS artifact URLs, and are returned only for known
slugs.

## Failure and cancellation model

Errors are typed and rendered as redacted human or JSON diagnostics. A stage does
not start its successor after failure. Cancellation removes temporary workspaces
and writes cancellation evidence plus the additive Apple side-effect journal; it
does not attempt destructive Apple rollback.

The final run report lives at `work/reports/<run-id>.json`. Reports and CI artifacts
contain provenance, hashes, decisions, timings, and stable resource IDs, but not
IPAs, profiles, private keys, passwords, or raw secret-bearing command output.
