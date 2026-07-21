# Migration from fastlane (spaceship) to App Store Connect CLI

## Summary

This project has been migrated from using fastlane's spaceship library to the [App Store Connect CLI](https://github.com/rorkai/App-Store-Connect-CLI) for provisioning profile management.

## Changes

### Removed
- `scripts/sync_profiles.rb` - Ruby script using spaceship
- `toml-rb` gem dependency

### Added
- `scripts/sync_profiles_asc.py` - Python script using `asc` CLI
- `asc` CLI installation in GitHub Actions workflow

### Modified
- `.github/workflows/sign-and-upload.yml` - Updated to use `asc` CLI
- `Gemfile` - Removed `toml-rb` dependency
- `README.md` - Updated documentation

## Benefits

1. **Faster execution** - Go binary vs Ruby interpreter
2. **Simpler dependencies** - No Ruby gems for API access
3. **Better JSON support** - Native JSON output from CLI
4. **Active maintenance** - `asc` CLI is actively maintained
5. **Cross-platform** - Works on macOS, Linux, Windows

## Local Testing

To test the new script locally:

```bash
# Install the checksum-verified 3.1.1 release for the local platform using the
# same canonical rorkai/App-Store-Connect-CLI release assets as CI.
asc version

# Set environment variables (asc CLI reads these automatically)
export ASC_KEY_ID="your_key_id"
export ASC_ISSUER_ID="your_issuer_id"
export ASC_PRIVATE_KEY_B64="$(base64 -i AuthKey_XXXX.p8 -o -)"
export ASC_BYPASS_KEYCHAIN=1

# Run check
uv run python scripts/sync_profiles_asc.py check

# Run sync
uv run python scripts/sync_profiles_asc.py
```

## Rollback

If you need to rollback to the old Ruby implementation:

```bash
git checkout HEAD~1 -- scripts/sync_profiles.rb .github/workflows/sign-and-upload.yml Gemfile README.md
git rm scripts/sync_profiles_asc.py
```

## Notes

- The old `sync_profiles.rb` is preserved in git history
- All functionality remains the same (check, sync, caching, device change detection)
- Environment variables remain unchanged
- Profile output format and location unchanged

---

# Migration from fastlane `resign` to zsign

## Summary

IPA re-signing has been migrated from fastlane's `resign` action to
[`zsign`](https://github.com/zhlynn/zsign). This removes the **last** Ruby
dependency from the project — there is no longer any `fastlane`, `bundler`, or
`Gemfile` involved anywhere in the pipeline.

## Changes

### Removed
- `Gemfile` / `Gemfile.lock` (fastlane was the only remaining gem)
- `apple-actions/import-codesign-certs` workflow step (no Keychain import needed)
- The "Resolve keychain path and discover identity" workflow step
- `find_bundle_exec()` and `discover_codesign_identity()` in `scripts/run_signing.py`
- `bundler` / `fastlane` installation from both workflows

### Added
- `Install zsign` workflow step — downloads `zsign`'s official prebuilt macOS
  arm64 binary (pinned via the `ZSIGN_VERSION` env, currently `v1.1.1`) and
  verifies its SHA256 checksum before use
- `find_zsign()` / `build_zsign_argv()` helpers in `scripts/run_signing.py`

### Modified
- `scripts/run_signing.py` — re-signs via `zsign -k <p12> -p <password> -m
  <profile> -o <out.ipa> <in.ipa>` instead of `fastlane run resign`. The P12 is
  decoded from `APPLE_DEV_CERT_P12_ENCODED` and passed straight to `zsign`.
- `.github/workflows/sign-and-upload.yml`, `.github/workflows/pr-checks.yml`
- `README.md`

## Why zsign

1. **No Ruby toolchain** — drops `bundler` + `fastlane` (hundreds of gems)
2. **No Keychain dance** — `zsign` signs directly from the P12 via OpenSSL, so
   the certificate import and codesign-identity discovery steps are gone
3. **Faster** — a small C++ binary; signing is quicker than fastlane's resign
4. **Cross-platform** — builds on macOS and Linux

## Signing credentials

`zsign` reads the P12 and its password directly. The script now requires
`APPLE_DEV_CERT_P12_ENCODED` (base64 P12) in addition to the existing
`APPLE_DEV_CERT_PASSWORD`. The password is passed as argv (never echoed to the
CI log). No other environment variables changed.

---

# Migration from macOS runner to Linux runner

## Summary

With Keychain and `fastlane` gone, nothing in the pipeline needs macOS anymore —
`zsign` signs iOS apps via OpenSSL rather than Apple's `codesign`/Security
framework. Both workflows now run on `ubuntu-latest` instead of `macos-latest`,
which GitHub bills at **~10× less** than a macOS runner.

## Changes

### Modified
- `.github/workflows/sign-and-upload.yml`, `.github/workflows/pr-checks.yml` —
  `runs-on: ubuntu-latest`
- Dependency install no longer uses Homebrew. Instead:
  - `sshpass` via `apt-get`
  - `asc` (App Store Connect CLI) — official prebuilt **Linux** binary
    (`asc_<ver>_linux_amd64`, pinned via `ASC_VERSION`), checksum-verified
  - `zsign` — official prebuilt **static musl Linux** binary
    (`zsign-linux-musl-static`, binary named `zsign-musl`), checksum-verified
- Checksum verification switched from `shasum -a 256 -c` to `sha256sum -c`
- Debug SSH: `dropbear` (started one-shot on `127.0.0.1:2222`, public-key only)
  replaces enabling macOS Remote Login; `cloudflared` is downloaded as a Linux
  binary instead of via Homebrew

### Removed
- All Homebrew usage (`brew trust`/`tap`/`install`) and the third-party taps
  (`rudrankriyam/tap`, `hudochenkov/sshpass`)
- `openssl@3` install — the static `zsign` binary has no runtime dependencies

## Why Linux

1. **Cost** — `ubuntu-latest` is billed at ~10× less than `macos-latest`; this
   workflow runs daily plus on demand, so the saving is the main driver
2. **Faster** — quicker runner startup and `apt`/direct downloads vs `brew update`
3. **Simpler & more auditable supply chain** — no Homebrew third-party taps;
   every downloaded binary (`asc`, `zsign`, `cloudflared`) is fetched by pinned
   version and SHA256-verified before use

## Notes

- `asc` already authenticated via env (`ASC_BYPASS_KEYCHAIN=1`), so its behaviour
  is identical on Linux. No secrets or environment variables changed.
- `scripts/run_signing.py` is unchanged — it only consumes `ZSIGN_BIN`/`PATH`.
- Worth verifying end-to-end once on Linux: that a Linux-`zsign`-signed IPA
  installs on a real device (zsign produces identical signatures cross-platform,
  but iOS signing is worth a smoke test).

---

# Migration from self-hosted server to serverless (Vercel + Cloudflare R2)

## Summary

Publishing no longer depends on the Plesk VPS (`itms.zeroclover.io` docroot,
`scp`/`sshpass`). Signed IPAs, icons, and the `site/apps.json` registry live on
Cloudflare R2 (`ipa.zeroclover.io`); the download page and the `itms.plist`
manifests are served by a Next.js app on Vercel (`web/`, same domain
`itms.zeroclover.io`). Full design: `docs/serverless-migration-plan.html`.

## Changes

### Removed
- `scp`/`sshpass` publishing (`ensure_remote_dir`, `scp_upload`, `deploy_site`),
  the `[site]` TOML table, and per-task `asset_server_path`
- `scripts/site_update.py` and the `site/` static download page (incl. the
  `apps.js` regex merge, the `?v=N` cache-buster, and the CI commit write-back —
  workflow permissions tightened to `contents: read`)
- `ASSETS_SERVER_*` secrets; the one-off icon-migration workflow after use

### Added
- `scripts/r2_store.py` — boto3 (S3-compatible) R2 wrapper: versioned immutable
  IPA keys (`apps/<slug>/<version>/<App>.ipa`), apps.json IO, stale-key cleanup
  whitelisted by the registry's references
- `scripts/apps_registry.py` — merges signing results into `site/apps.json` on
  R2 (per-slug refresh, `name` from `app_name`)
- `web/` — Next.js 16 download page: app grid + `/apps/<slug>/itms.plist`
  dynamic manifests + `/api/revalidate` (shared secret) on-demand ISR
- Per-task `slug` field; `R2_*` / `VERCEL_REVALIDATE_SECRET` env vars

### Modified
- `scripts/run_signing.py` — publishes to R2, merges the registry, triggers
  revalidation, then deletes stale keys (any failure skips cleanup)
- `.github/workflows/sign-and-upload.yml`, `.github/workflows/pr-checks.yml`

## Why serverless

1. **No server to maintain** — the VPS only served static assets; R2 serves
   them with zero egress fees, Vercel serves the KB-sized page/manifests
2. **No cache-buster hacks** — versioned immutable keys + on-demand ISR
   revalidation replace `?v=N` and manual purges
3. **Single data source** — apps.json on R2 feeds both the page and the
   manifests; no CI commit write-back, no drift between page and plist

## Rollback

Within 48h of DNS cutover: point `itms.zeroclover.io` back to the VPS record
(the server stays read-only during that window). Code-level rollback is a
revert of the merge commit. After the server is decommissioned (requires two
clean weeks), rollback is no longer possible.
