# Signing pipeline troubleshooting

## Release asset selection

`source.asset-match-count` with zero or multiple candidates means the configured
`release_glob` is not exact for that release. Inspect the reported asset names and
commit a reviewed selector that matches exactly one IPA. LiveContainer standard
uses `LiveContainer.ipa`; do not broaden it to `*.ipa`, because the same release
also contains `LiveContainer+SideStore.ipa`.

## New or missing bundle rules

An upstream extension added, removed, or renamed in inventory must stop signing.
Compare the reported profile-bearing graph with `tasks.signing.bundles`. Add an
exact source Bundle ID rule, target mapping, capability policy, entitlement mode,
App ID, and profile; then repeat automated and device acceptance. Never let an
unknown extension inherit the root profile.

## App Groups and capabilities

An App Group `manual-required` finding is expected when the public API cannot
inspect the container relationship. An Account Holder/Admin must register the
group and associate it with every listed App ID, then record the non-secret
evidence. Do not create one App Group for each Keychain Group: LiveContainer uses
one App Group and 128 local `keychain-access-groups` strings.

For an unsupported or managed capability, follow the exact planner remediation.
Do not substitute a similarly named Portal switch or call an undocumented API.
Clinical Health Records and HealthKit background delivery are local HealthKit
template values here; Keychain Sharing is also local. Profiles must still
authorize every final value.

## Profile authorization mismatch

`apple.profile-entitlement-unauthorized` identifies the first key/value outside
the mapped profile. Confirm the target App ID, capability state, App Group
association, certificate, enabled devices, and profile type. Generate an additive
replacement after any capability change; keep the invalid historical profile.
Do not delete entitlements merely to make signing pass unless a reviewed policy
declares the drop and its rationale.

## 128 Keychain Groups

The profile may authorize Keychain access with a wildcard, but the signed root and
LiveProcess executables must each contain exactly the 128 target-team values from
`com.kdt.livecontainer.shared` through `.127`. A count of 1 or 2 indicates
profile-only signing; a count of 0 indicates lost entitlement material. Confirm
the per-profile entitlement backend, production template, target-team prefix, and
profile authorization. Launch and Share should retain their profile defaults,
not the 128 root/process list.

## XML/DER disagreement

If XML and DER entitlement evidence differ, stop before publication. Record the
architecture, executable path, both evidence hashes, tool versions, and source
SHA. Reproduce with the pinned Linux inspector and independent macOS `codesign`
oracle. Do not choose one representation as authoritative or suppress the check.

## Nested signature failure

Use the verification report's deepest failing path. Confirm the plan includes
every framework, dylib, extension, and nested app, that signing order is deepest
first and root last, and that each profile-bearing bundle embeds its own mapped
profile. Re-inventory the output for graph parity. A stale, ad-hoc, wrong-team, or
unplanned nested signature blocks promotion while the prior published object and
registry entry remain active.
