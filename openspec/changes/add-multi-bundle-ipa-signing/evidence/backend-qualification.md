# Signing backend qualification status

Recorded on 2026-07-21 while starting task section 2.

## Verified upstream behavior

- The latest stable `zhlynn/zsign` release is `v1.1.1`.
- The release README explicitly documents repeated `-m` arguments for extension profiles.
- The same CLI exposes only one global `-e` entitlement argument; it does not document a per-bundle entitlement-map argument.
- The checksum-published macOS arm64 archive SHA-256 is `f50da4b23c807e4e43b2ef5f16cc90bb1aec2ab790d07a2380e16440d767f029`.
- The extracted macOS arm64 `zsign` executable SHA-256 is `7e95a575570708c961f363a620148247b963b2081692d1bd4941d6e3df83bd66`.
- Running the executable reports `version: 1.1.1`; its help confirms repeated profile inputs are accepted while entitlements remain a single option.
- The checksum-published Linux musl archive SHA-256 is `9880b0e1290dea211481fd031bcca8d0d7f3f09ba1c6a89743b3422df1ac14b9`.

## Qualification blocker

The repository and local provisioning-profile directory contain no development `.mobileprovision` fixtures, and the current process has no App Store Connect credentials or P12 input. Two local code-signing identities exist, but a signing identity alone cannot create the four distinct Apple-authorized profiles required by tasks 2.1–2.5.

The hard gate specifically requires private or sanitized real development profiles whose App IDs and entitlements deliberately differ across root, process, Launch, and Share bundles. Generating those profiles would require selecting a developer team and creating or changing four Apple App IDs/capabilities, which is external account mutation and cannot be inferred from the repository. Synthetic CMS files or ad-hoc signatures would not prove Apple's authorization behavior and therefore cannot satisfy the gate.

No section 3 implementation may start until the required private fixture inputs are provided or an authorized private qualification job can generate them, the Linux result is compared with the macOS `codesign` oracle, and an ADR is accepted.

## Required private inputs

Provide these through a private, non-artifact-retained environment rather than committing them:

1. One development signing identity export (P12 plus password) matching the profiles.
2. Four development profiles for deliberately distinct target App IDs: root, LiveProcess-equivalent, Launch-equivalent, and Share-equivalent.
3. Profiles that exercise the reviewed App Group mapping; root/process must additionally authorize the sensitive policy and exact target-team keychain-group contract used by the qualification.
4. At least one enabled registered iOS device in every profile.
5. Approval to run the private qualification without publishing its IPA, profiles, P12, private key, or raw entitlement/profile artifacts.
