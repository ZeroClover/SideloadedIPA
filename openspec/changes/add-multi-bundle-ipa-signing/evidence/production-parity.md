# Production package-engine parity

Recorded from the development branch
`feat/add-multi-bundle-ipa-signing` on 2026-07-21 UTC.

## Accepted production run

[Sign & Upload run 29876354164](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29876354164)
completed successfully at commit `b6fff17`. The workflow installed the
checksum-pinned toolchain, synchronized the five single-bundle production
profiles, ran `sideloadedipa run --publish`, independently reopened and verified
every signed IPA, published the batch, revalidated the registry, and saved the
cache only after the signer succeeded.

| Task | Version | Artifact SHA-256 prefix | Verification report SHA-256 prefix | Published object |
| --- | --- | --- | --- | --- |
| JHenTai | 8.0.14 | `b3e1f846` | `7b38292b` | `apps/JHenTai/8.0.14/b3e1f8460c0d-JHenTai.ipa` |
| Eros FE | 1.9.2 | `1858b009` | `3785745b` | `apps/fehviewer/1.9.2/1858b009cfb6-Eros_FE.ipa` |
| Asspp | 4.2.0 | `b8dfd555` | `ed4c0979` | `apps/Asspp/4.2.0/b8dfd5553d8f-Asspp.ipa` |
| PiliPlus | 2.1.0 | `41b472b9` | `a9efde60` | `apps/PiliPlus/2.1.0/41b472b91183-PiliPlus.ipa` |
| StikDebug | 3.1.6 | `32aa2fc3` | `867699b8` | `apps/StikDebug/3.1.6/32aa2fc32ac5-StikDebug.ipa` |

The five task results committed one registry document with SHA-256
`a02e2f44bab13f977c593893f702975116ea23d927f48256023d093c83fc09e0`.
Only after registry publication and revalidation succeeded did cleanup remove
the replaced unhashed IPA objects and two obsolete icon objects.

## Parity and rollback acceptance

The accepted run preserved each task's reviewed release selection, target bundle
identifier, signed version metadata, configured icon behavior, public slug, and
registry entry while replacing mutable filename keys with the package engine's
content-addressed keys. Its all-or-nothing batch transaction also corrected the
legacy partial-publication behavior observed in failed run `29874713723`, where
three tasks became visible before the remaining batch failed.

[PR Checks run 29876354016](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29876354016)
passed at the production-migration commit. A report artifact exposed progress
text preceding JSON on stdout; commit `e695aa3` redirected progress to stderr,
added a machine-readable output regression test, and passed
[PR Checks run 29876780453](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29876780453).

Production rollback now means republishing the last verified content-addressed
registry/object set. It no longer depends on a second signing implementation.
The duplicate `run_signing` and `apps_registry` implementations, compatibility
wrappers, and the per-task engine flag are therefore removed. Profile refresh
and version/device change detection remain because the package workflow still
uses those focused adapters.

LiveContainer remains explicitly publication-disabled. This production parity
acceptance does not satisfy its physical-device, publication-enable, or
scheduled/upstream-transition gates.
