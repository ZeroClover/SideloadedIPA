# Migration from fastlane (spaceship) to App Store Connect CLI

## Summary

This project has been migrated from using fastlane's spaceship library to the [App Store Connect CLI](https://github.com/rudrankriyam/App-Store-Connect-CLI) for provisioning profile management.

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
# Install asc CLI
brew tap rudrankriyam/tap
brew install asc

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
  arm64 binary (pinned via the `ZSIGN_VERSION` env, currently `v1.0.4`) and
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
