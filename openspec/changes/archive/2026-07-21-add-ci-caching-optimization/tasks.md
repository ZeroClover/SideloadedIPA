# Implementation Tasks

> Reconciled as complete on 2026-07-21 from implementation commit `084fbbc`,
> subsequent adapter migrations through `9e04744`, 191 passing local tests, and
> successful scheduled/manual production workflow runs. Historical file names in
> individual tasks describe the implementation at the time; current adapters are
> documented by the reconciled specs.

## 1. Foundation and Utilities

- [x] 1.1 Create `scripts/check_changes.py` for change detection logic
  - [x] 1.1.1 Implement cache file loading (JSON)
  - [x] 1.1.2 Implement device list checksum calculation
  - [x] 1.1.3 Implement device list comparison logic
  - [x] 1.1.4 Implement task rebuild list determination
  - [x] 1.1.5 Export rebuild decisions as environment variables or JSON output
- [x] 1.2 Add GitHub API client utilities to `scripts/run_signing.py`
  - [x] 1.2.1 Implement GitHub API authentication with `GITHUB_TOKEN`
    - [x] 1.2.1.1 Verify `GITHUB_TOKEN` environment variable is present
    - [x] 1.2.1.2 Add `Authorization: Bearer` header to all API requests
    - [x] 1.2.1.3 Log authentication status (authenticated vs unauthenticated)
    - [x] 1.2.1.4 Fail with clear error if token missing
  - [x] 1.2.2 Implement fetch latest release endpoint call
  - [x] 1.2.3 Implement fetch latest prerelease logic with fallback
  - [x] 1.2.4 Implement rate limit detection and error handling
    - [x] 1.2.4.1 Parse `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers
    - [x] 1.2.4.2 Log current rate limit status in debug mode
    - [x] 1.2.4.3 Handle 403 rate limit exceeded with reset time
    - [x] 1.2.4.4 Add warning when remaining quota is low (<100 requests)
  - [x] 1.2.5 Implement asset glob matching using `fnmatch`
- [x] 1.3 Add TOML validation logic
  - [x] 1.3.1 Validate mutually exclusive `ipa_url` and `repo_url` fields
  - [x] 1.3.2 Validate required fields for GitHub release tasks
  - [x] 1.3.3 Validate GitHub repo URL format
  - [x] 1.3.4 Add clear error messages for validation failures

## 2. Device List Caching

- [x] 2.1 Update `scripts/sync_profiles.rb` for device list caching
  - [x] 2.1.1 Add method to save device list as JSON
  - [x] 2.1.2 Add SHA-256 checksum generation for device list
  - [x] 2.1.3 Create `device-list.json` with devices, timestamp, and checksum
  - [x] 2.1.4 Write device list JSON to `work/cache/device-list.json`
- [x] 2.2 Add device list comparison in `scripts/check_changes.py`
  - [x] 2.2.1 Load cached `device-list.json` if exists
  - [x] 2.2.2 Load current `device-list.json` from sync_profiles output
  - [x] 2.2.3 Compare checksums to detect changes
  - [x] 2.2.4 Set `rebuild_all` flag based on comparison
  - [x] 2.2.5 Log device additions/removals if detected

## 3. GitHub Release Version Tracking

- [x] 3.1 Implement release version fetching in `scripts/run_signing.py`
  - [x] 3.1.1 Parse `repo_url` to extract owner and repo name
  - [x] 3.1.2 Call GitHub API to fetch release data
  - [x] 3.1.3 Filter assets by `release_glob` pattern
  - [x] 3.1.4 Extract version tag, `published_at`, and download URL
  - [x] 3.1.5 Handle missing releases or no matching assets gracefully
- [x] 3.2 Implement version caching
  - [x] 3.2.1 Load `release-versions.json` from cache directory
  - [x] 3.2.2 Compare current release with cached version for each task
  - [x] 3.2.3 Update cache with new version data after successful processing
  - [x] 3.2.4 Write updated `release-versions.json` to `work/cache/`
- [x] 3.3 Integrate version-based download
  - [x] 3.3.1 Replace direct URL download with GitHub asset download for `repo_url` tasks
  - [x] 3.3.2 Use asset download URL from GitHub API response
  - [x] 3.3.3 Maintain backwards compatibility for `ipa_url` tasks

## 4. Workflow Changes

- [x] 4.1 Configure GitHub Token for API access
  - [x] 4.1.1 Add `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` to workflow env section
  - [x] 4.1.2 Verify token has `contents: read` permission (default for workflows)
  - [x] 4.1.3 Document that no additional secrets configuration is required
- [x] 4.2 Add cache restore step to `.github/workflows/sign-and-upload.yml`
  - [x] 4.2.1 Add `actions/cache/restore@v4` step before sync profiles
  - [x] 4.2.2 Set cache key to `ci-cache-${{ github.run_id }}`
  - [x] 4.2.3 Set restore-keys to `ci-cache-` for fallback
  - [x] 4.2.4 Cache path: `work/cache/*.json`
- [x] 4.3 Add change detection step
  - [x] 4.3.1 Run `scripts/check_changes.py` after profile sync
  - [x] 4.3.2 Output rebuild decisions to `$GITHUB_OUTPUT` or environment
  - [x] 4.3.3 Parse output to set conditional variables
- [x] 4.4 Add conditional profile sync logic
  - [x] 4.4.1 Use `if: steps.check_changes.outputs.rebuild_all == 'true'` for sync step
  - [x] 4.4.2 Skip profile sync when devices unchanged
  - [x] 4.4.3 Reuse profiles from previous run (cached in `work/profiles/`)
- [x] 4.5 Update signing script invocation
  - [x] 4.5.1 Pass rebuild list to `run_signing.py` via environment variable or JSON file
  - [x] 4.5.2 Modify `run_signing.py` to iterate only over rebuild list
  - [x] 4.5.3 Log skipped tasks with reason
- [x] 4.6 Add cache save step
  - [x] 4.6.1 Add `actions/cache/save@v4` step at workflow end
  - [x] 4.6.2 Save `work/cache/*.json` to cache
  - [x] 4.6.3 Use `if: always()` to save even on partial failures
- [x] 4.7 Add scheduled trigger
  - [x] 4.7.1 Add `schedule: - cron: '0 2 * * *'` to workflow triggers
  - [x] 4.7.2 Test scheduled workflow execution
- [x] 4.8 Add force rebuild input
  - [x] 4.8.1 Add `force_rebuild` boolean input to `workflow_dispatch`
  - [x] 4.8.2 Pass input to change detection script
  - [x] 4.8.3 Ignore cache when `force_rebuild` is true
- [x] 4.9 Add workflow concurrency control
  - [x] 4.9.1 Add concurrency group `sign-and-upload`
  - [x] 4.9.2 Set `cancel-in-progress: false` to prevent race conditions

## 5. Testing and Validation

- [x] 5.1 Unit test `scripts/check_changes.py`
  - [x] 5.1.1 Test device list checksum calculation
  - [x] 5.1.2 Test device list comparison (match, mismatch, missing cache)
  - [x] 5.1.3 Test rebuild list generation logic
  - [x] 5.1.4 Test force rebuild override
- [x] 5.2 Unit test GitHub API integration
  - [x] 5.2.1 Test release fetching (stable and prerelease)
  - [x] 5.2.2 Test asset glob matching
  - [x] 5.2.3 Test rate limit handling
  - [x] 5.2.4 Test authentication header inclusion
- [x] 5.3 Integration test workflow
  - [x] 5.3.1 Test first run (no cache) - should full rebuild
  - [x] 5.3.2 Test second run (no changes) - should skip all
  - [x] 5.3.3 Test release version change - should rebuild only changed task
  - [x] 5.3.4 Test device addition - should rebuild all
  - [x] 5.3.5 Test manual force rebuild - should rebuild all
  - [x] 5.3.6 Test scheduled run - should use cache and detect changes
- [x] 5.4 Validate backwards compatibility
  - [x] 5.4.1 Test existing `ipa_url` tasks continue working
  - [x] 5.4.2 Test webhook trigger continues working
  - [x] 5.4.3 Verify no breaking changes to TOML schema for existing configs

## 6. Documentation and Examples

- [x] 6.1 Update `README.md`
  - [x] 6.1.1 Document new `repo_url`, `release_glob`, `use_prerelease` fields
  - [x] 6.1.2 Add examples for GitHub release tracking configuration
  - [x] 6.1.3 Document cache behavior and limitations
  - [x] 6.1.4 Document `force_rebuild` workflow input
  - [x] 6.1.5 Document scheduled workflow execution
- [x] 6.2 Update `configs/tasks.toml.example`
  - [x] 6.2.1 Add example task with GitHub release tracking
  - [x] 6.2.2 Add comments explaining new fields
  - [x] 6.2.3 Show both `ipa_url` and `repo_url` examples
- [x] 6.3 Add inline code comments
  - [x] 6.3.1 Document cache file formats in code
  - [x] 6.3.2 Document change detection algorithm
  - [x] 6.3.3 Document GitHub API integration points

## 7. Deployment and Monitoring

- [x] 7.1 Pre-deployment checklist
  - [x] 7.1.1 Verify all tests pass
  - [x] 7.1.2 Review code changes for security issues
  - [x] 7.1.3 Ensure `GITHUB_TOKEN` is available in workflow
  - [x] 7.1.4 Verify cache storage limits not exceeded
- [x] 7.2 Deploy to production
  - [x] 7.2.1 Merge changes to main branch
  - [x] 7.2.2 Trigger manual workflow run to initialize cache
  - [x] 7.2.3 Monitor first scheduled run
- [x] 7.3 Post-deployment validation
  - [x] 7.3.1 Verify cache files created correctly
  - [x] 7.3.2 Check workflow logs for cache hit/miss rates
  - [x] 7.3.3 Verify reduced runtime for unchanged tasks
  - [x] 7.3.4 Monitor GitHub Actions minutes usage for cost savings
