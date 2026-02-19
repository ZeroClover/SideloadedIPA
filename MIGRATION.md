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

# Authenticate
echo "$ASC_PRIVATE_KEY" | base64 -d > /tmp/asc_key.p8
asc auth login \
  --name "Local" \
  --key-id "$ASC_KEY_ID" \
  --issuer-id "$ASC_ISSUER_ID" \
  --private-key /tmp/asc_key.p8

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
