# Workflow migration evidence

Recorded on 2026-07-21 from development branch
`feat/add-multi-bundle-ipa-signing`.

## File-backed stage contract

`FileStageManifestStore` writes canonical stage manifests atomically with mode
`0600`, hashes task names for filesystem addressing, reopens each predecessor
from disk, and rejects schema, identity, timestamp, or digest tampering. The
workflow fixture traverses source, inventory, policy, resource plan/apply,
signing plan, sign, verify, and publish in order. Its redacted summary SHA-256
was `ed58dfae10e7547449bd9e4fc6a4d29dd58dffa8eaa9cbd8a4da616eff37f918`.

## CI validation

- [PR Checks run 29866681057](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29866681057)
  passed at commit `b9ae5ec`: 765 package tests with the 95% coverage gate,
  package and legacy type checks, formatting, web tests/build, checksum and
  runtime checks for zsign/ASC, actionlint `1.7.12` with Linux shellcheck, and
  the file-backed workflow fixture.
- The preceding run `29866450879` correctly failed on shellcheck `SC2086` for
  an unquoted actionlint executable path. Commit `b9ae5ec` fixed the command
  and upgraded `setup-node`, `setup-uv`, and `actions/cache` to the current
  stable releases verified through the GitHub Releases API.
- The PR workflow has explicit read-only contents permission, per-job timeouts,
  cancellable PR concurrency, no Apple/R2 secrets, and the shared public-key-only
  SSH Debug action only for an explicit manual dispatch.

## Read-only shadow

[Run 29866453662](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29866453662)
passed at commit `2f79ff6`. It inspected and planned every current production
task using real read credentials while production signing and every mutation
job were skipped. The summary recorded inventory exit `1` for the already
reviewed unsigned-input entitlement blockers, Apple plan exit `0`, Apple apply
and signing/verification as skipped, and publication as disabled.

The inventory report retained the previously reviewed SHA-256
`1aa87a1731a9ad7a20256b9df207eb0ed80ab0de5330c1a5dbd497c5913c9f7b`;
the Apple plan report SHA-256 was
`0bd14f3c978839e6c7096bd27dcc8e0fa38e3f46ccb0ca5e941cbcafe3b9c765`.
Only four redacted JSON reports were retained for three days.

## Private canary

[Run 29866684148](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29866684148)
passed at commit `b9ae5ec`. The Linux job completed checksum/version preflight,
four-bundle inventory, read-only Apple validation, the upstream negative
control, the qualified per-profile-entitlement sign/verify path, and an explicit
publication-disabled gate. The independent macOS `codesign` oracle and final
comparison also passed.

The final comparison recorded `linux_contract_pass`, `codesign_contract_pass`,
`profile_mapping_matches`, `root_last`, and `xml_der_evidence_complete` as true.
Its SHA-256 was
`a3a3cae9d59e7979f1584b0285d09c7f9db53957d0eb43c4bac48bd5a1bf85a1`.
The publication-disabled manifest SHA-256 was
`fe49ad7fe73f23e5422e3d673047e0c0437ea4c1c6827f1954ea32ed00c62474`.

No R2 credential entered the canary jobs, the production sign/upload job was
skipped, and the retained files contained no P12, private key, raw profile,
credential marker, extracted workspace, or signed IPA. Linux/macOS private
materials were removed on their runners. Linux/macOS summaries were retained
for one day and the comparison for seven days.

## Cache and artifact policy

The production cache uses namespace `pipeline-cache-v2` and fingerprints OS,
the lockfile, task configuration, package source, and the qualified zsign
patch. Restore is optimization-only; save requires both overall success and a
successful signer step. The prior `if: always()` save is gone. Report uploads
refer only to allowlisted paths under `runner.temp`, use short retention, and
never include workspaces or signing material.
