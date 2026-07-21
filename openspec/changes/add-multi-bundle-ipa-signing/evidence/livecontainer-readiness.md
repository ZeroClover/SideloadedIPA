# LiveContainer readiness evidence

Recorded 2026-07-21 for the standard four-bundle `LiveContainer.ipa` task. This
document contains no credential, raw profile, certificate, device identifier, or
signed IPA material.

## Operator prerequisites

The operator confirmed that `group.io.zeroclover.app.livecontainer` is registered
and associated with all four explicit App IDs:

- `io.zeroclover.app.livecontainer`
- `io.zeroclover.app.livecontainer.LiveProcess`
- `io.zeroclover.app.livecontainer.LaunchAppExtension`
- `io.zeroclover.app.livecontainer.ShareExtension`

The operator also confirmed App Groups on all four App IDs, and HealthKit plus
Increased Memory Limit on root and LiveProcess. Keychain Sharing is a standard
local entitlement. Clinical Health Records and HealthKit background delivery are
reviewed HealthKit entitlement values in the repository template, not separate
Developer Portal capabilities.

## Read-only production plan

[Shadow run 29867553848](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29867553848)
used commit `8974fc6` and performed no Apple, signing, cache-success, R2, registry,
or revalidation mutation. It resolved LiveContainer 3.8.0's exact
`LiveContainer.ipa` asset with source SHA-256
`b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6`.

The inventory contained exactly four profile-bearing bundles. The Apple plan
contained four exact existing App IDs, fourteen capability no-ops, four profile
reconciliation operations, and the intended App Group mapping for each target.
App Group association remains reported as `manual-required` because the verified
public API cannot inspect that relationship; the operator evidence above closes
the human prerequisite without introducing a private API fallback.

## Profiles and automated canary

[Qualification run 29833282121](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29833282121)
created additive `Dev 2` replacements without deleting invalid historical
profiles. It validated the exact App ID, configured certificate, ten enabled
devices, profile type, validity window, App Group, and bundle-specific entitlement
authorization for all four profiles.

[Production-policy canary 29868226408](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29868226408)
used commit `ab1c815` and loaded the checked-in LiveContainer task and entitlement
template before signing. Results:

- four adjacent profile/entitlement pairs and complete root-last signing order;
- exact common App Group on root, LiveProcess, Launch, and Share;
- HealthKit, health-records access, HealthKit background delivery, increased
  memory, and exactly 128 local Keychain Groups on root and LiveProcess;
- no root-only HealthKit or increased-memory values on Launch or Share;
- matching Linux and independent macOS profile mapping, XML/DER entitlement,
  nested-signature, graph, and package evidence;
- no R2 credentials in the canary job and `publication = "disabled"` in its
  retained report.

The downloaded retained reports were scanned for private-key, P12, raw profile,
and credential markers; none were present.

## Remaining acceptance gates

- Install and exercise the canary on a registered physical device.
- Keep `publication_enabled = false` until the device checklist passes and a
  separate reviewed configuration change enables it.
- Observe a scheduled refresh and a real upstream release transition.
- Keep `LiveContainer+SideStore.ipa` absent from production configuration until
  its fifth App ID/profile, widget policy, and device acceptance are completed.
