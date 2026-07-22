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

The production task records that reviewed confirmation with
`manual_app_group_associations = ["shared"]`. This converts only the
API-unobservable relationship operation to `no-op`; generated profiles must
still authorize the exact group for all four target Bundle IDs.

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

## Final installable canary

[Run 29879583177](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29879583177)
at commit `bfc50de` produced the final private device canary after hardening the
production verifier. All four synchronized profiles matched their inputs and
their bundle-local SHA-256 `CodeResources` entries on both Linux and the
independent macOS oracle. Linux installable-artifact verification, macOS strict
nested verification, and the cross-platform comparison all passed.

The IPA SHA-256 is
`287750f58b112a7e6512bbe29898e3102f14cc045e6dc7a65aa57f90a02ca106`.
Its source, plan, graph, and verification-report SHA-256 values are respectively
`b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6`,
`9e7dd2caf9c0d126e139f85d8e7096ead26043180979ac7cba833dbe0c52e53e`,
`d79adcf653cba4eee8fe499c91d4d4296f1f09bf4fcc89a8a4c1c8cbd1a0fe31`,
and `68714d6eb865aebcda1bd331b17b042d730378512b3336246f07f89284113073`.
Publication was disabled. The retained CI artifact expires after one day; the
same verified IPA is available locally at `work/signed/LiveContainer.ipa` for
the registered-device checklist.

## Registered-device acceptance

On 2026-07-22, the operator installed the final canary identified above on a
registered physical device and confirmed that every reviewed device test passed:
install and launch, Launch extension, Share extension, LiveProcess/JIT-less
operation, shared App Group storage, approved HealthKit behavior, and the
128-keychain-group diagnostic. No device identifier or other private device
metadata is retained in this evidence.

This confirmation applies to the exact standard `LiveContainer.ipa` source,
four-bundle graph, signing policy, and signed IPA digests recorded above. It does
not apply to `LiveContainer+SideStore.ipa` or a future acceptance-relevant source,
graph, policy, or checklist change.

## Production publication enablement

After the automated and registered-device gates passed,
`publication_enabled = true` was enabled for the standard LiveContainer task on
the development branch. Production publication remains subject to the complete
fresh profile, signing, verification, and atomic publication gates on every run.

[Production run 29882807965](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29882807965)
used enablement commit `7349d8d` and force-rebuilt the six-task atomic batch.
LiveContainer profile synchronization, signing, verification, upload, registry
mutation, revalidation, cache save, and notification all passed. The redacted
run report recorded:

- source SHA-256
  `b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6`;
- unchanged four-bundle graph SHA-256
  `d79adcf653cba4eee8fe499c91d4d4296f1f09bf4fcc89a8a4c1c8cbd1a0fe31`;
- plan SHA-256
  `9e7dd2caf9c0d126e139f85d8e7096ead26043180979ac7cba833dbe0c52e53e`;
- published artifact SHA-256
  `9e86bfe4739d42371a27beab49c427d77cc4a79178cdf2370c1b5e197d63204a`;
- verification-report SHA-256
  `926d22b29347b7cfbc7b698d4a4b5bc1c5d4a52f40aeb143cb351a961abf6722`;
- immutable object
  `apps/LiveContainer/3.8.0/9e86bfe4739d-LiveContainer.ipa` and registry SHA-256
  `f8b44ec1116a9efa11073185d0e9090258c74dfc394c970951949cb44d2c5ab2`.

An independent public read returned HTTP 200 for the immutable object and the
live `site/apps.json` entry identified `io.zeroclover.app.livecontainer`, version
3.8.0, and the same object URL. The SideStore asset was not selected or
published.

### App icon publication regression

The first production registry entry exposed an empty `iconUrl` because the
LiveContainer task omitted the deliberately explicit `icon_path` setting.
Commit `0eed465` selected the release-tag-aligned 1024x1024 upstream master at
`Resources/Assets.xcassets/AppIcon.appiconset/AppIcon1024.png` and added a
production-configuration regression assertion.

[PR Checks run 29883264735](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29883264735)
passed all four jobs. [Sign & Upload run 29883266633](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29883266633)
then fetched the icon from the 3.8.0 tag, normalized it to a 512x512 PNG, uploaded
`apps/LiveContainer/icon-c20526ad070a.png`, updated the registry, and revalidated
the page. Independent public checks confirmed HTTP 200, `image/png`, 512x512
RGBA, and SHA-256
`c20526ad070a551a50fb89c4f505b47beff14bf22425853944cd3febe7d2e796`.
Both the live registry and rendered page reference that exact icon URL.

## Post-archive operating constraints

The maintainer accepted the scheduled-refresh and future-upstream-transition
observation as complete for archive purposes on 2026-07-22 without claiming
those time-dependent events occurred. Existing automated graph-change and
publication-rollback coverage supplies the fail-closed evidence; the real events
remain routine production monitoring.

Keep `LiveContainer+SideStore.ipa` absent from production configuration until
its fifth App ID/profile, widget policy, and device acceptance are completed.
