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

## Remaining acceptance gates

- Observe a scheduled refresh and a real upstream release transition.
- Keep `LiveContainer+SideStore.ipa` absent from production configuration until
  its fifth App ID/profile, widget policy, and device acceptance are completed.
