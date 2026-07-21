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
- [Development-branch qualification run 29826420918](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29826420918) checksum-verified that archive on `ubuntu-latest`, reported zsign `1.1.1`, and recorded Linux executable SHA-256 `1f8c8c1576284a395450d8bf26b1e63d19822dac924fe020110d0ab803a44d24`.
- The isolated job confirmed the documented CLI shape: repeated `-m` profile arguments are supported and `-e` remains one global entitlement argument. The production sign/upload job was skipped, no artifact was uploaded, and the private runner directory was removed on failure.

## Qualification blocker

The repository and local provisioning-profile directory contain no development `.mobileprovision` fixtures. CI has valid App Store Connect and P12 credentials, but the read-only qualification run proved that only the root target App ID currently exists. The exact `LiveProcess`, `LaunchAppExtension`, and `ShareExtension` target App IDs are absent, so no corresponding development profiles can exist yet.

After explicit operator authorization, [qualification run 29826749998](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29826749998) created the three missing explicit App IDs through the canonical ASC 3.1.1 API path and then re-listed all four exact identifiers. The operation was additive; no identifier was deleted or renamed.

[Qualification run 29826924161](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29826924161) matched the configured P12 to exactly one Apple development certificate, selected the enabled iPhone/iPad set, and created one active `IOS_APP_DEVELOPMENT` profile for each of root, LiveProcess, Launch, and Share. Each downloaded profile decoded successfully and contained the configured certificate and target application identifier. Private profile/P12 bytes remained runner-local and were deleted by the cleanup step.

The first additive run used qualification-specific display names that did not match the account's existing LiveContainer naming convention. With explicit operator authorization, [qualification run 29831432451](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29831432451) preflighted exact identifiers, legacy display names, and profile relationships; deleted only the four agent-created legacy-named profiles and three agent-created nested App IDs; retained the pre-existing root App ID; and recreated the nested App IDs and all four profiles with standard names.

[Read-only qualification run 29831598319](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29831598319) then re-listed Apple state without apply or reset flags and verified the persisted names:

- App IDs: `LiveContainer LiveProcess`, `LiveContainer LaunchAppExtension`, and `LiveContainer ShareExtension`.
- Profiles: `LiveContainer Dev`, `LiveContainer LiveProcess Dev`, `LiveContainer LaunchAppExtension Dev`, and `LiveContainer ShareExtension Dev`.

The post-create validation stopped at the intended capability boundary: all four App IDs currently expose only `IN_APP_PURCHASE`, and the four profiles have no common authorized App Group. These profiles are qualification evidence but cannot satisfy the LiveContainer contract. App Group registration/association and approval-gated root/process capabilities must be completed before replacement profiles are generated; the pipeline will not publish or weaken the entitlement assertions in the meantime.

After the operator registered a team-owned App Group and assigned the App ID capabilities, [read-only qualification run 29833005095](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29833005095) reported `APP_GROUPS`, `HEALTHKIT`, and `INCREASED_MEMORY_LIMIT` on root and LiveProcess, and `APP_GROUPS` on Launch and Share. `Clinical Health Records` and HealthKit background delivery are local entitlement-template values under HealthKit, not separate Developer Portal capabilities. The capability changes invalidated all four earlier profiles as expected, leaving zero active development profiles and requiring additive replacements.

The first replacement attempt retained the invalid profiles as required but proved that Apple also retains their display names, so the base profile names could not be reused. The qualification helper now chooses the next numeric revision without deleting history. [Qualification run 29833282121](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29833282121) created the four `Dev 2` replacement profiles and completed with `ready: true`:

- all four profiles authorize `group.io.zeroclover.app.livecontainer`, use the configured certificate, and share 10 enabled iOS devices;
- root and LiveProcess profiles contain HealthKit, `healthkit.access`, HealthKit background delivery, increased memory limit, App Groups, `get-task-allow`, and keychain access-group authorization;
- Launch and Share contain App Groups, `get-task-allow`, and keychain access-group profile defaults without the root-only HealthKit or increased-memory keys;
- the runner deleted all downloaded profile and P12 material after validation and skipped the production sign/upload job.

The hard gate now has four private real development profiles whose App IDs and entitlement authorization deliberately differ across root, process, Launch, and Share bundles. The remaining section 2 work must construct the synthetic bundle graph, test repeated-profile zsign behavior, and compare it with the macOS oracle; synthetic CMS files or ad-hoc signatures alone would not prove Apple's authorization behavior.

[Qualification run 29833747687](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29833747687) built the deterministic synthetic four-bundle IPA on `ubuntu-latest` from only the four audited Mach-O executables in the checksum-pinned LiveContainer 3.8.0 asset. The generated archive contains four minimal profile-bearing bundles and eight files total, has SHA-256 `75fe30fff97b883b374288b31509d9063d7df73e3442aec65ace717dd54020f8`, and matched the locally generated digest. The same run revalidated all four private profiles with `ready: true`; neither the source asset nor private profiles are committed or retained as CI artifacts.

[Linux backend exercise 29834532465](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29834532465) invoked checksum-verified zsign v1.1.1 with four repeated `-m` arguments and no `-e`. The four output bundles embedded byte-identical copies of their intended input profiles. Root and LiveProcess received the profile-derived HealthKit, `health-records`, background-delivery, increased-memory, App Group, and development defaults; Launch and Share received the common App Group and development defaults without root-only HealthKit or increased-memory keys.

The upstream profile-only mode failed the exact functional contract: all four signed bundles contained two profile-default keychain groups, while root and LiveProcess require the reviewed 128 exact target-team groups. Both violations were reported independently, the signed IPA remained private, cleanup ran, and publication was skipped. This proves that repeated-profile selection works but profile-derived entitlements cannot replace per-bundle local entitlement documents.

The private fixture inputs and independent comparison are now complete. No section 3 implementation may start until the task 2.6 backend ADR is accepted and its mandatory contract suite passes for the selected backend.

## Private input controls

The qualification used these inputs only through the private, non-artifact-retained runner workspace:

1. The existing CI development signing identity export matching the profiles.
2. The four validated `Dev 2` profiles for root, LiveProcess, Launch, and Share.
3. Version-controlled local entitlement templates that request the reviewed App Group mapping and exact target-team 128-keychain-group contract.
4. The existing common enabled-device set.
5. A private non-publishing qualification job that retains no IPA, profile, P12, private key, or raw entitlement/profile artifacts.

## Independent macOS comparison

[Qualification run 29837799905](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29837799905) compared the reviewed Linux result with a separate `macos-15` `codesign` oracle using the same four private profiles and configured development identity. The macOS job imported the P12 into a temporary isolated keychain, embedded the exact role-specific profile in each bundle, signed Launch, LiveProcess, Share, then the root app, and passed per-bundle strict verification plus root `--deep --strict` verification.

The oracle extracted and compared each signed bundle's XML entitlements, raw DER entitlements, DER code-directory slot, embedded-profile digest, and nested-signature result. Root and LiveProcess contained the exact 128 target-team Keychain Groups, Launch and Share retained their own profile-derived entitlement documents without root-only HealthKit or increased-memory values, and all four embedded profiles matched their inputs. The final redacted comparison recorded `codesign_contract_pass: true`, `profile_mapping_matches: true`, `root_last: true`, and `xml_der_evidence_complete: true`.

Only redacted summaries were uploaded. Their SHA-256 digests were:

- Linux zsign summary: `72dfc56667fdf2b1cc29ac47e85b04280c0befbaf369766f848f0e1645a8ecf8`.
- macOS codesign summary: `9223a282c261bd873da480bf7673091dbd1b9f7b5653d2b32ad257f709c2f102`.
- comparison summary: `27448d36705db69c9839c2b911869ba000c54cc0041dff948ddf0121a1d38921`.

This completes task 2.5 and preserves the observed backend boundary for the task 2.6 decision: upstream zsign v1.1.1 maps repeated profiles correctly but cannot express the required per-bundle local entitlement documents, while the independent macOS oracle satisfies the full qualification contract.
