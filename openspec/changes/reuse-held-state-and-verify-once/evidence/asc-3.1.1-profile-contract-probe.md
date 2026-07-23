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

## Commands probed

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

No ASC credential variables or local ASC configuration were available. It was
therefore not possible to establish that every live list item contains
`attributes.profileContent`, nor that a live included view carries all three
relationship payloads inline.

## Production CI debug follow-up (pre-fix)

A production `workflow_dispatch` run with `debug=true` was started and
completed successfully:

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29981889230
- Commit: `351820490f933199cc2316f53df3071b4e924cd5`
- The Apple plan and apply steps both completed successfully, establishing that
  their step-scoped ASC credentials were valid.
- The published Cloudflare tunnel was reached with the repository's configured
  SSH key, and the runner exposed the pinned Linux `asc` 3.1.1 binary.
- The then-current debug action removed all ASC credential variables before
  starting the SSH server, tunnel, and hold process. Dropbear also cleared its
  server process environment by default. The SSH login environment contained no
  ASC credential variables, ASC config, or `.p8` key.
- Re-running the read-only list command over SSH exited with status 3 and the
  same `missing authentication` result.

No attempt was made to recover secrets from completed step processes or bypass
the debug action's credential boundary.

The debug implementation is now corrected so a manually dispatched production
debug session receives the production step's explicit ASC, signing, R2, GitHub,
Vercel, and webhook environment. The action no longer unsets credentials and
starts Dropbear with `-e`, which preserves the caller environment for the SSH
child. PR validation debug sessions do not receive these production-secret
mappings. A run from the corrected workflow is still required before replacing
the conservative D2 fallback with an authenticated response-shape contract.

## Selected adapter contract

Because the primary response-shape contract could not be authenticated, the
implementation takes D2's fail-closed fallback:

- keep one `profiles view --include bundleId,certificates,devices` read per
  enumerated profile;
- remove the three independent `profiles links` calls;
- keep `_validated` downloading and validating profile content;
- use one targeted included view to verify a successful create instead of
  re-enumerating the profile collection.

This still removes three calls per existing profile and bounds successful-create
verification to one profile resource without relying on an unobserved response
shape.
