# Signing pipeline operator runbook

## Command boundary

Install exactly the locked project environment before local operations:

```bash
uv sync --frozen
```

The package CLI composes `inspect`, `plan`, `sync`, `sign`, and `run`. Both
`sign` and `run` independently verify the signed package before returning
success; `run --publish` additionally performs verified atomic publication.
The standalone `verify` command remains reserved because the production command
does not persist private verification inputs between invocations.

## Read-only inspection

Inspect one task before any Apple work:

```bash
uv run sideloadedipa inspect --task LiveContainer --json > inventory.json
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
uv run sideloadedipa plan --task LiveContainer --json > apple-plan.json
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
uv run sideloadedipa sync --task LiveContainer --apply --json > apple-apply.json
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

Production publication uses `sideloadedipa run --publish` in the scheduled/manual
`Sign & Upload IPAs` job for tasks with `publication_enabled = true`.
LiveContainer remains excluded while its flag is false. The enforced order is:
verified output first, immutable upload second, atomic registry update third,
revalidation fourth, stale cleanup last.

## Retry and rollback

- Retry transient reads, downloads, and content-addressed uploads with the same
  operation identity. Do not blindly retry an ambiguous Apple create result;
  perform an exact lookup first.
- On cancellation, remove temporary extracted/signing workspaces. Record Apple
  resources already created and leave them in place.
- A failed sign, verify, upload, or registry update must leave the prior R2 object
  and registry entry active. Never delete the old object before registry success
  and revalidation.
- Roll back a task by restoring its last reviewed configuration and published registry entry.
  Keep `publication_enabled = false` for a new multi-bundle task until a fresh
  automated canary and physical-device checklist pass.

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
