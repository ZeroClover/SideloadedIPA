# Final automated acceptance matrix

Initially recorded 2026-07-21 for implementation commit `fba98ac` and extended
after production package-engine acceptance. The change remains unarchived
because the time/device-dependent LiveContainer gates listed below are still
open.

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

[Sign & Upload run 29876354164](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29876354164)
passed all five existing production tasks through the package engine and its
atomic verified-publication service at commit `b6fff17`. Source selection,
signed metadata, icon behavior, content-addressed R2 keys, registry publication,
revalidation, and cleanup evidence are recorded in `production-parity.md`.
After acceptance, the duplicate signing/publication scripts and per-task legacy
engine switch were removed; rollback retains the prior verified registry and
objects rather than invoking a second signing implementation.

## Required before archive

- 11.7: registered-device acceptance.
- 11.8: a separate reviewed change enabling LiveContainer publication.
- 11.9: one scheduled refresh and one real upstream-release transition.
- 12.10: archive only after all preceding evidence is complete.
