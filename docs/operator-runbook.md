# Signing pipeline operator runbook

## Command boundary

Install exactly the locked project environment before local operations:

```bash
uv sync --frozen
```

The default validation command keeps the 95 percent package gate and terminal
missing-line report without writing a persistent HTML artifact:

```bash
uv run pytest
```

Generate the same scoped coverage as HTML only when diagnosing coverage locally:

```bash
uv run pytest --cov-report=term-missing --cov-report=html
```

Validate the download application against its bundled registry only through the
explicit non-deployment fixture mode:

```bash
cd web
npm ci
npm test
APPS_DATA_MODE=fixture npm run build
```

Production Vercel deployments use `APPS_DATA_MODE=origin`, an HTTPS
`R2_APPS_JSON_URL`, `REVALIDATE_SECRET`, and `SITE_PUBLIC_BASE_URL`. Vercel's
`VERCEL_ENV=production` guard rejects fixture mode. Registry reads use the
persistent Next.js data cache with the `apps` tag; authenticated revalidation
marks the tag stale with the `max` stale-while-revalidate profile. A refresh
failure therefore leaves the prior valid cached value eligible, while an
initial origin, JSON, or schema failure is surfaced rather than rendered as an
empty catalog.

The package CLI composes `inspect`, `plan`, `sync`, `sign`, `verify`, `publish`,
and `run`. Each visible command persists a canonical, redacted stage manifest
under `work/pipeline/<run-id>/`; its successor refuses to run if the predecessor
is missing, unsuccessful, or no longer forms the same digest chain. Use one
unique `--run-id` for every attempt and pass it to every command in that attempt.

## Read-only inspection

Inspect one task before any Apple work:

```bash
run_id="local-$(date +%Y%m%d%H%M%S)"
uv run sideloadedipa inspect --run-id "$run_id" --task LiveContainer --json
```

The command downloads the uniquely selected asset, validates the ZIP, discovers
all signable code recursively, and emits source, graph, and entitlement evidence.
Exit `0` means every selected inventory passed; exit `1` means one or more task
reports contain a typed failure. No Apple, cache-success, signing, R2, registry,
or revalidation mutation is permitted.

## Apple plan and apply

Provide the App Store Connect environment and the development P12 inputs used by
CI, then plan one task:

```bash
uv run sideloadedipa plan --run-id "$run_id" --task LiveContainer --json
```

Review every `manual-required` and `blocked` operation. App Group registration
and association are Account Holder/Admin work in Developer Portal or Xcode when
the public API cannot inspect the relationship. Clinical Health Records,
HealthKit background delivery, and Keychain Sharing are reviewed local
entitlement values, not additional Portal resources in this workflow.

After App Group registration and every required App ID assignment have been
reviewed, record the configured alias in
`tasks.signing.manual_app_group_associations`. This closes only the relationship
operation that the public API cannot observe; profile synchronization still
validates the exact App Group entitlement for every affected bundle.

Apply only after the inventory and plan have been reviewed:

```bash
uv run sideloadedipa sync --run-id "$run_id" --task LiveContainer --apply --json
```

Apply is additive: it may create an explicit App ID, enable an allowlisted API
capability, and create/download a replacement development profile. It never
deletes App IDs or profiles, disables capabilities, removes App Group
relationships, or revokes certificates. A capability change invalidates old
profiles; retain them and let the reconciler choose the next numeric profile
revision.

## Backend requalification

Requalify before accepting a change to the zsign version, executable digest,
source commit/archive digest, patch set, profile/entitlement command shape,
per-bundle entitlement behavior, or supported platform. The reviewed identity
and trigger surface live in `patches/zsign/qualification-contract.json`.

PR validation builds the checksum-pinned patched binary and runs the
deterministic four-bundle fixture through the real production `ZsignBackend`.
That contract proves ordered profile/entitlement pairs, distinct embedded
profiles and entitlements, a profile-free helper, complete signatures, and a
tampered-output failure.

The single operator entry point is:

```bash
uv run sideloadedipa-qualify-backend \
  --run-id "backend-$(date +%Y%m%d%H%M%S)" \
  --evidence work/qualification/backend-qualification.json
```

Export `ZSIGN_BIN` and its exact `ZSIGN_SHA256` plus the normal Apple/P12
environment first. The command reuses production `inspect`, read-only `plan`,
and `sync`; add `--apply` only after reviewing the Apple plan when current
profiles must be created or refreshed. It has no deletion or resource-reset
mode.

A complete requalification also requires the independent macOS codesign oracle.
On the Mac holding the temporary test identity, pass `--codesign-identity` and
`--codesign-keychain`; alternatively pass a retained `--oracle-summary` that is
bound to the exact current fixture digest. On Linux, or on macOS without the
identity, keychain, `codesign`, and `security`, the command writes
`status = manual-gate-unmet` evidence and exits `3`; it never reports the absent
oracle as success.

The schema-versioned evidence contains only the fixture, contract, backend,
plan, output, oracle, and comparison documents plus their SHA-256 digests. It
does not retain IPAs, profiles, private keys, certificate passwords, paths, or
raw command output. Exit `0` is reserved for a digest-bound oracle comparison
that passes.

## Signing, verification, and publication

For a reviewed new task, set `publication_enabled = true` in `configs/tasks.toml`
and exercise the complete production path through workflow dispatch:

```bash
gh workflow run sign-and-upload.yml \
  --ref <reviewed-branch> \
  -f force_rebuild=true \
  -f debug=false
```

The workflow runs inventory, Apple plan/apply, signing, independent verification,
R2 upload, atomic registry promotion, revalidation, and stale cleanup. This tests
the public ITMS installation path instead of a private non-publishing substitute.
If any gate fails, the new task is not advertised and existing registry entries
remain protected. For interactive diagnosis after a failure, use `-f debug=true`;
the terminal SSH step retains the actual runner environment while its long-lived
processes exclude production credentials.

The production job exposes the remaining boundaries explicitly:

```bash
uv run sideloadedipa sign --run-id "$run_id" --task LiveContainer --json
uv run sideloadedipa verify --run-id "$run_id" --task LiveContainer --publish --json
uv run sideloadedipa publish --run-id "$run_id" --task LiveContainer --json
```

Omit `--publish` from `verify` for a non-publishing run; successful verification
then promotes its cache evidence immediately. With `--publish`, cache promotion
waits until publication and revalidation have succeeded. `publish` independently
reopens and verifies each IPA again before upload. The enforced publication order
is verified output first, immutable upload second, atomic registry update third,
revalidation fourth, stale cleanup last. A failed batch compensates only newly
uploaded objects that are no longer referenced and preserves the prior registry.

`sideloadedipa run --apply [--publish]` remains the convenience composition for
local use. CI intentionally invokes the visible stages above so its logs and
artifacts show every boundary.

## Cache and retained evidence

`work/cache/signing-index.json` is a canonical digest-verified index; signed IPA
paths are content-addressed by the complete signing fingerprint. A matching
fingerprint is only a candidate cache hit: production checks current profile and
certificate prerequisites, confirms the stored verification-report digest, and
reopens the cached IPA through the full independent verification gate. Invalid
or tampered candidates are reported as `cache-rejected` and rebuilt.

The final redacted report is `work/reports/<run-id>.json`. It contains measured stage timings,
source and graph provenance, bundle/profile mappings, capability decisions,
non-secret tool and certificate fingerprints, cache decisions, verification,
and publication outcomes. Per-stage manifests and cache decisions remain under
`work/pipeline/<run-id>/`; each task also retains a canonical signing report with
actual per-node backend evidence. Because zsign signs the graph in one subprocess,
per-node `duration_seconds` is `null`; the total backend duration remains measured.
CI uploads these JSON files even after failure.

## Retry and rollback

- Retry transient reads, downloads, and content-addressed uploads with the same
  operation identity. Do not blindly retry an ambiguous Apple create result;
  perform an exact lookup first.
- On cancellation, remove temporary extracted/signing workspaces. The command
  writes `work/reports/<run-id>-cancellation.json` and the side-effect journal
  records Apple resources already created. These files are evidence only: they
  do not roll back, delete, or compensate Apple resources. Leave additive
  resources in place, inspect them, and reconcile by exact identity on retry.
- A failed sign, verify, upload, or registry update must leave the prior R2 object
  and registry entry active. Never delete the old object before registry success
  and revalidation. If compensating cleanup itself fails, the diagnostic lists
  every remaining IPA and icon key that was newly uploaded by that attempt.
- Roll back a task by restoring its last reviewed configuration and the last
  verified registry document. A new task becomes public only when its explicitly
  enabled production run passes every verification and publication boundary. Do
  not reuse manifests across run IDs or manually mark a failed cache record
  successful.

## Profile refresh and cleanup

Run a read-only plan after certificate, enabled-device, capability, App Group,
entitlement-template, or source-graph changes. Replace profiles that no longer
authorize the exact policy or fall inside the refresh threshold. Optional cleanup
of old profiles, App IDs, groups, or certificates is manual and outside CI; first
prove that no active task or rollback artifact references the resource.

Apple notes that capability changes invalidate affected profiles and documents
App Group assignment as an additional capability step:
[enable app capabilities](https://developer.apple.com/help/account/identifiers/enable-app-capabilities/).

## Device verification

After the verified production run publishes LiveContainer to the ITMS service,
test all of the following against the exact source SHA and policy commit:

- installation and normal launch;
- Launch extension;
- Share extension;
- LiveProcess/JIT-less behavior;
- shared App Group storage;
- approved HealthKit behavior;
- diagnostic confirmation of all 128 signed Keychain Groups.

Any source asset, bundle graph, identifier mapping, entitlement policy, or profile
change should be retested through the newly published ITMS entry.
