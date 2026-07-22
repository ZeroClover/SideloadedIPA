# Signing pipeline operator runbook

## Command boundary

Install exactly the locked project environment before local operations:

```bash
uv sync --frozen
```

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

## Signing, verification, and publication

Run a private, non-publishing LiveContainer canary through workflow dispatch:

```bash
gh workflow run sign-and-upload.yml \
  --ref <reviewed-branch> \
  -f multi_bundle_canary=true \
  -f debug=false
```

The canary uses the production LiveContainer policy and template, four real
profiles, the checksum-qualified backend, and an independent macOS oracle. It has
no R2 credentials and retains only redacted JSON. For interactive device
handoff, add `-f debug=true`; the workflow's SSH step keeps that runner alive.

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

The final redacted report is `work/reports/<run-id>.json`. It contains timings,
source and graph provenance, bundle/profile mappings, capability decisions,
non-secret tool and certificate fingerprints, cache decisions, verification,
and publication outcomes. Per-stage manifests and cache decisions remain under
`work/pipeline/<run-id>/`; CI uploads these JSON files even after failure.

## Retry and rollback

- Retry transient reads, downloads, and content-addressed uploads with the same
  operation identity. Do not blindly retry an ambiguous Apple create result;
  perform an exact lookup first.
- On cancellation, remove temporary extracted/signing workspaces. The command
  writes `work/reports/<run-id>-cancellation.json` with Apple resources already
  created; leave those additive resources in place and reconcile them on retry.
- A failed sign, verify, upload, or registry update must leave the prior R2 object
  and registry entry active. Never delete the old object before registry success
  and revalidation.
- Roll back a task by restoring its last reviewed configuration and the last
  verified registry document. Keep `publication_enabled = false` for a new
  multi-bundle task until a fresh automated canary and physical-device checklist
  pass. Do not reuse manifests across run IDs or manually mark a failed cache
  record successful.

## Profile refresh and cleanup

Run a read-only plan after certificate, enabled-device, capability, App Group,
entitlement-template, or source-graph changes. Replace profiles that no longer
authorize the exact policy or fall inside the refresh threshold. Optional cleanup
of old profiles, App IDs, groups, or certificates is manual and outside CI; first
prove that no active task or rollback artifact references the resource.

Apple notes that capability changes invalidate affected profiles and documents
App Group assignment as an additional capability step:
[enable app capabilities](https://developer.apple.com/help/account/identifiers/enable-app-capabilities/).

## Device acceptance

For LiveContainer, record all of the following against the exact source SHA and
policy commit before enabling publication:

- installation and normal launch;
- Launch extension;
- Share extension;
- LiveProcess/JIT-less behavior;
- shared App Group storage;
- approved HealthKit behavior;
- diagnostic confirmation of all 128 signed Keychain Groups.

Any source asset, bundle graph, identifier mapping, entitlement policy, profile,
or acceptance-contract change makes the prior device record stale.
