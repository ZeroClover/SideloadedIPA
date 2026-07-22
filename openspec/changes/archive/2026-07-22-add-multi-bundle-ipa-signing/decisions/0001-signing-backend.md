# ADR 0001: Linux zsign with paired per-profile entitlements

- Status: Accepted
- Date: 2026-07-21
- Decision owners: `add-multi-bundle-ipa-signing`

## Context

The production workflow already runs on Linux. Upstream zsign v1.1.1 supports repeated `-m` profile arguments, but its CLI passes one global `-e` entitlement file to every signing asset. Private qualification proved that profile-only signing maps all four profiles correctly but gives root and LiveProcess only the two profile-default Keychain Groups instead of the required exact 128 local values.

The zsign v1.1.1 implementation already stores the provisioning profile and entitlement document together in each `ZSignAsset`, selects that asset by the bundle's application identifier, and signs child bundles before the root. Its CLI initializes every repeated profile with the same final `-e` path. It also writes an extension's selected `embedded.mobileprovision` only after generating that extension's `CodeResources`, leaving the installed profile outside the SHA-256 resource envelope.

An independent `macos-15` oracle proved that separate entitlement documents are authorized by the real profiles and that the resulting XML entitlements, DER entitlement slots, embedded profiles, nested signatures, and root-last sealing are valid.

## Decision

Use zsign v1.1.1 plus the repository's minimal upstreamable per-profile-entitlement patch as the first production `SigningBackend` adapter. Keep production signing on Linux and keep macOS `codesign` as an independent qualification oracle.

The backend supply chain SHALL:

- download source commit `d6e929c97b5b564c2cc1f82afe226a44da7149a0`, the commit tagged `v1.1.1`, from the canonical `zhlynn/zsign` repository;
- require source archive SHA-256 `d9b1577da22a766eabbe1eeb5fc17cc2c4f060e3411a20713f9814fc30f6a670` before extraction;
- apply `patches/zsign/v1.1.1-per-profile-entitlements.patch` without fuzz and fail if it no longer applies;
- build and require version `1.1.1+sideloadedipa.2`, recording the actual executable SHA-256 in every result;
- preserve one `-e` as the legacy global entitlement behavior, pair repeated `-e` values with repeated `-m` values by argument order, and reject repeated count mismatches before signing;
- write each selected profile before generating that bundle's `CodeResources`, and require the backend contract to verify its exact `files2` SHA-256 entry;
- pass every planned profile and entitlement document as an adjacent `-m PROFILE -e ENTITLEMENTS` pair in deterministic plan order.

The patch changes CLI collection, validation, construction of existing `ZSignAsset` values, and the timing of the existing profile write. It does not change Mach-O signature generation, profile selection, or bundle traversal. The timing change intentionally makes `CodeResources` cover the final profile bytes instead of preserving zsign's invalid post-envelope write.

## Mandatory contract

The following assertions are release gates for this adapter and any replacement:

1. Every profile-bearing bundle embeds the exact planned profile and receives the exact planned entitlement document.
2. Root and LiveProcess contain the intended HealthKit values, increased-memory value, App Group, development defaults, and exactly 128 target-team Keychain Groups.
3. Launch and Share contain their own intended App Group/development policy and do not inherit root-only values.
4. The backend rejects missing, extra, or mismatched profile/entitlement inputs before signing.
5. Every nested subtree is signed before its parent and the root app is signed last.
6. Independent macOS inspection confirms XML semantics, a nonempty DER entitlement representation and slot, byte-identical embedded profiles, and strict nested-signature verification.
7. Any backend version, source checksum, patch-application, feature-probe, or executable provenance mismatch blocks signing.

[Qualification run 29839575241](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29839575241) passed this complete contract with the real four-profile fixture. The Linux result recorded `contract_pass: true`, count-mismatch rejection, signing order Launch/LiveProcess/Share/root, and executable SHA-256 `811ba4b2304ba3262de4256ef2f920065ab6f12f4c8ea04be9d27e4ed0acef0a`. Its four per-bundle entitlement summaries matched the independent macOS oracle, whose XML/DER and nested-signature assertions also passed.

## Cost and runtime

The accepted qualification measured 31 seconds for the Linux job, including canonical source download, patching, compilation, Apple-state/profile validation, the expected upstream failure, and the successful extended-backend sign. The independent macOS job took 21 seconds and the comparison took 7 seconds. The patch build itself took about 9 seconds on the hosted Linux runner.

The repository is public, so standard GitHub-hosted runners currently do not consume billable minutes. If the workflow becomes billable, GitHub's published standard-runner rates on 2026-07-21 are USD 0.006/minute for Linux and USD 0.062/minute for macOS, with each job rounded up to a whole minute. The macOS rate is therefore about 10.3 times the Linux rate. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) and [runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing).

Keeping Linux also avoids moving the existing download, cache, R2, registry, and publication path to a different runner or transferring private intermediate signing material between jobs.

## Consequences

- The `SigningBackend` interface remains replaceable, but its first adapter is explicitly `zsign 1.1.1+sideloadedipa.2`, not unmodified upstream zsign.
- The repository owns a small third-party patch and must requalify it whenever zsign, the compiler image, OpenSSL, or signature verification behavior changes.
- Production may cache a built binary only under a key containing the source commit, source SHA-256, patch SHA-256, build inputs, target platform, and backend contract version. Cache hits still require version and executable-hash evidence.
- When a later stable upstream zsign release supports distinct per-bundle entitlements, the patch SHALL be removed only after that unmodified release passes the same contract and macOS oracle.
- A macOS production adapter remains the correctness fallback if the patch becomes unmaintainable, but selecting it requires a new ADR with fresh runtime and cost evidence.

## Rejected alternatives

- **Unmodified profile-only zsign:** rejected by the exact 128-Keychain-Group contract.
- **One global `-e`:** rejected because it would leak root-only policy into Launch and Share or strip required root/process values.
- **Multiple recursive zsign invocations:** rejected because a later root pass can replace nested signatures and entitlement evidence.
- **macOS production fallback now:** correct in qualification, but unnecessary after the Linux extension passed; it adds a platform split and a materially higher billable rate.
- **A different Linux signer:** no demonstrated benefit over the narrowly scoped extension of the already characterized backend.
