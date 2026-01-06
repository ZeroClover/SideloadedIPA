# Change: Add CI Caching and Smart Execution Optimization

## Why

Currently, the CI workflow executes the full signing and publishing process for every IPA on every run, regardless of whether the source IPA has been updated or device configurations have changed. This results in:

- Unnecessary GitHub Actions runtime consumption
- Redundant profile regeneration and IPA re-signing
- Longer execution times for unchanged applications
- Wasted bandwidth downloading and uploading unchanged IPAs

By implementing intelligent caching and version tracking, we can minimize CI execution to only process what has actually changed, reducing costs and improving efficiency.

## What Changes

- **GitHub Release Integration**: Add support for tracking GitHub releases as IPA sources instead of direct URLs
  - New TOML fields: `repo_url`, `release_glob`, `use_prerelease` (conflicts with `ipa_url`)
  - Automatic version tracking via GitHub API
  - Cache release versions and update timestamps

- **Device List Caching**: Cache App Store Connect device lists using GitHub Actions cache
  - Compare cached device list with current state
  - Trigger full rebuild when devices are added/removed

- **Smart Execution Logic**: Implement conditional processing based on change detection
  - Device changes → full rebuild of all IPAs
  - Release version changes → rebuild only affected IPA
  - New tasks or non-tracked sources → always rebuild

- **Scheduled Cache Refresh**: Add daily scheduled workflow run
  - Ensures cache stays fresh even without manual triggers
  - Automatically detects and processes new releases

- **Cache State Files**: Two JSON cache files stored in GitHub Actions cache
  - `release-versions.json`: Maps task names to release metadata (version, published_at, download_url)
  - `device-list.json`: Snapshot of all registered devices from ASC API

## Impact

- **Affected specs**:
  - `task-configuration` (new spec)
  - `github-release-tracking` (new spec)
  - `device-list-caching` (new spec)
  - `workflow-optimization` (new spec)
  - `scheduled-execution` (new spec)

- **Affected code**:
  - `.github/workflows/sign-and-upload.yml` - Add caching, scheduling, and conditional execution
  - `scripts/sync_profiles.rb` - Add device list comparison logic
  - `scripts/run_signing.py` - Add GitHub API integration and version tracking
  - `configs/tasks.toml` - Schema extension for GitHub release tracking
  - New script: `scripts/check_changes.py` - Determine which tasks need execution

- **Deployment notes**:
  - First run after deployment will always execute full rebuild (no cache)
  - Subsequent runs will benefit from caching
  - No impact on manual `workflow_dispatch` behavior (can force full rebuild)
  - Existing `ipa_url` configuration remains fully supported
