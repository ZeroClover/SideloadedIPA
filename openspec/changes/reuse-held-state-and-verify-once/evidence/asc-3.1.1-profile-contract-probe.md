# ASC 3.1.1 Profile Contract Probe

Date: 2026-07-23

## Binary identity

- Repository: `rorkai/App-Store-Connect-CLI`
- Release: `3.1.1`
- Asset: `asc_3.1.1_macos_arm64`
- Reviewed and observed SHA-256:
  `47d9be058359ab29c4f562361abfed710b7f24acdaa79c78777bc6e25e118fef`
- Version output:
  `3.1.1 (commit: cf3457b, date: 2026-07-20T10:27:57Z)`

The release asset was downloaded to a private temporary directory, its digest
was checked before execution, and no credential or profile payload was written
to this change directory.

## Local binary probe

The pinned binary's help accepts both intended commands:

```text
asc profiles list --profile-type IOS_APP_DEVELOPMENT --output json
asc profiles view --id PROFILE_ID --include bundleId,certificates,devices
```

The `view` help specifically lists `bundleId`, `certificates`, and `devices` as
supported included resources.

The read-only list probe exited with status 3 before reaching App Store Connect:

```text
Error: profiles list: missing authentication.
```

This local environment had no ASC credential variables or local ASC
configuration, so the response-shape probe continued in an authenticated
production debug session.

## Authenticated production CI debug probe

A corrected production `workflow_dispatch` run with `debug=true` was started
and completed successfully:

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29988342462
- Commit: `7111977683f5508d778e4afb7d245291a6c418f8`
- Binary: `asc` 3.1.1 (`cf3457b`)
- The production Apple, signing, publication, and notification credential
  variables were present in the SSH child. Only variable names and presence
  were checked; no value was printed or persisted.
- The debug action performed no credential cleanup and started Dropbear with
  environment preservation enabled.

Raw command responses were written only under a runner-private temporary
directory and deleted before the tunnel was stopped. The probe output retained
only JSON keys, counts, booleans, lengths, and content digests.

### List response

`asc profiles list --profile-type IOS_APP_DEVELOPMENT --output json` returned:

- root keys `data`, `links`, and `meta`;
- 20 profile resources;
- the same complete attribute keys for every resource:
  `createdDate`, `expirationDate`, `name`, `platform`, `profileContent`,
  `profileState`, `profileType`, and `uuid`;
- non-empty `profileContent` for all 20 resources; every value decoded under
  strict base64 validation (encoded lengths ranged from 17,156 to 38,772);
- relationship keys `bundleId`, `certificates`, and `devices`, whose values
  contained only `links` and `meta`; none contained relationship `data`.

The pinned CLI's `profiles list --help` has no `--include` option.

### Included view response

For one profile selected without retaining its identifier,
`asc profiles view --id <id> --include bundleId,certificates,devices --output
json` returned:

- root keys `data`, `included`, and `links`;
- the complete profile attributes including `profileContent`;
- relationship `data` shaped as one bundle object, a certificate array, and a
  device array;
- one bundle, one certificate, and ten devices in the sampled response;
- matching `included` resources for every relationship identifier.

A plain `profiles view --id <id> --output json` also returned the complete
attributes. Strictly decoded profile bytes from the list item, included view,
and plain view were byte-identical. Only the sampled byte count and SHA-256
were compared; the profile body was never printed.

## Selected adapter contract

The authenticated response shape selects D2's primary path:

- decode and retain attributes, `profileContent`, and its digest from the one
  list response;
- use one `profiles view --include bundleId,certificates,devices` read per
  enumerated profile only for relationship identifiers;
- remove the three independent `profiles links` calls;
- require list and included-view content digests to match;
- make `_validated` validate the digest-bound held bytes with no content
  download;
- use one targeted included view to verify a successful create instead of
  re-enumerating the profile collection.

Malformed base64, missing content, malformed relationship identifiers, and
list/view content mismatches all remain fail-closed adapter errors.
