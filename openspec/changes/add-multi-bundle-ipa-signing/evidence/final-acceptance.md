# Final automated acceptance matrix

Initially recorded 2026-07-21 for implementation commit `fba98ac` and extended
after production package-engine acceptance. Registered-device acceptance passed
on 2026-07-22 and the reviewed LiveContainer production flag is enabled. The
change remains unarchived until the time-dependent production observations below
are complete.

## Local matrix

- `SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION=1 uv run pytest`: 775 passed,
  zero skipped, 95.21% package coverage. This includes both checksum-pinned
  LiveContainer 3.8.0 assets, failure injection, cache, profile, backend,
  verification, security, publication rollback, and legacy characterization.
- `uv run mypy src/sideloadedipa`: strict success for 74 package modules.
- Black and isort checks passed for compatibility scripts and non-legacy package
  modules.
- `uv lock --check`: the committed dependency lock is current.
- Five-iteration benchmark: inventory median 3.978 ms versus 7.517 ms before
  redundant-scan removal; planning 0.358 ms; profile reuse 0.021 ms; signing
  4.480 ms; verification 0.318 ms; cache hit 0.041 ms; one inventory tree scan
  and zero profile-create API calls.
- `openspec validate add-multi-bundle-ipa-signing --strict` and
  `git diff --check`: success.

## CI matrix

[PR Checks run 29868954698](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29868954698)
at `fba98ac` passed all four jobs:

- checksum/version-qualified zsign and App Store Connect CLI installation;
- package tests and 95% coverage gate, formatting, package mypy, and the
  explicitly non-blocking pre-existing legacy-script mypy audit;
- actionlint, embedded shell checks, and file-backed workflow manifest fixture;
- web plist tests and production build.

[Private canary run 29868957053](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29868957053)
at the same commit passed production-policy Linux signing, independent macOS
`codesign` verification, and cross-platform comparison. Publication was disabled
and no R2 credential was present.

## Side-effect review

The final automated matrix performed no Apple mutation: no run set
`qualification_apply`, and the production Apple evidence came from read-only
planning/profile download. It performed no R2 upload, registry mutation,
revalidation, stale-object cleanup, cache-success promotion, or signed-IPA
artifact retention. Retained reports are redacted JSON with limited retention.

## Production migration

[Sign & Upload run 29877347948](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29877347948)
passed all five existing production tasks through the package engine and its
atomic verified-publication service at commit `b21ab06`, after legacy engine
removal. Source selection,
signed metadata, icon behavior, content-addressed R2 keys, registry publication,
revalidation, and cleanup evidence are recorded in `production-parity.md`.
After acceptance, the duplicate signing/publication scripts and per-task legacy
engine switch were removed; rollback retains the prior verified registry and
objects rather than invoking a second signing implementation.

## Registered-device and publication gates

On 2026-07-22, the operator confirmed that the exact final canary recorded in
`livecontainer-readiness.md` passed the complete registered-device checklist.
The standard LiveContainer task was consequently enabled for production through
its reviewed `publication_enabled` configuration. SideStore remains out of
production scope.

[PR Checks run 29882806411](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29882806411)
passed all four jobs for enablement commit `7349d8d`.
[Sign & Upload run 29882807965](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29882807965)
then passed the forced six-task production batch. Its redacted report marked
LiveContainer 3.8.0 published at the content-addressed object
`apps/LiveContainer/3.8.0/9e86bfe4739d-LiveContainer.ipa`, with artifact SHA-256
`9e86bfe4739d42371a27beab49c427d77cc4a79178cdf2370c1b5e197d63204a`.
The public object returned HTTP 200 and the live registry referenced the same
URL and target bundle ID.

The initially empty LiveContainer `iconUrl` was corrected by commit `0eed465`.
PR Checks run 29883264735 and production run 29883266633 passed; the live
registry and rendered page now reference the verified 512x512 content-addressed
PNG `apps/LiveContainer/icon-c20526ad070a.png`.

## Required before archive

- 11.9: one scheduled refresh and one real upstream-release transition.
- 12.10: archive only after all preceding evidence is complete.

## Final profile-seal regression

At commit `bfc50de`, local regression completed with 722 passed, 2 skipped, and
95.04% package coverage; strict package mypy, the focused backend/oracle suite,
patch application, macOS compilation of `1.1.1+sideloadedipa.2`, formatting,
lockfile, OpenSpec strict validation, and diff checks passed.

[PR Checks run 29879583158](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29879583158)
passed all four jobs. [Private canary run 29879583177](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29879583177)
then passed the installable Linux canary, private artifact upload, independent
macOS strict oracle, and cross-platform evidence comparison. This closes the
automated 11.6 gate; the later operator confirmation and reviewed configuration
close 11.7 and 11.8 without changing the upstream-transition or archive gates
above.
