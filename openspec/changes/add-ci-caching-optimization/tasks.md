# Implementation Tasks

## 1. Foundation and Utilities

- [ ] 1.1 Create `scripts/check_changes.py` for change detection logic
  - [ ] 1.1.1 Implement cache file loading (JSON)
  - [ ] 1.1.2 Implement device list checksum calculation
  - [ ] 1.1.3 Implement device list comparison logic
  - [ ] 1.1.4 Implement task rebuild list determination
  - [ ] 1.1.5 Export rebuild decisions as environment variables or JSON output
- [ ] 1.2 Add GitHub API client utilities to `scripts/run_signing.py`
  - [ ] 1.2.1 Implement GitHub API authentication with `GITHUB_TOKEN`
    - [ ] 1.2.1.1 Verify `GITHUB_TOKEN` environment variable is present
    - [ ] 1.2.1.2 Add `Authorization: Bearer` header to all API requests
    - [ ] 1.2.1.3 Log authentication status (authenticated vs unauthenticated)
    - [ ] 1.2.1.4 Fail with clear error if token missing
  - [ ] 1.2.2 Implement fetch latest release endpoint call
  - [ ] 1.2.3 Implement fetch latest prerelease logic with fallback
  - [ ] 1.2.4 Implement rate limit detection and error handling
    - [ ] 1.2.4.1 Parse `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers
    - [ ] 1.2.4.2 Log current rate limit status in debug mode
    - [ ] 1.2.4.3 Handle 403 rate limit exceeded with reset time
    - [ ] 1.2.4.4 Add warning when remaining quota is low (<100 requests)
  - [ ] 1.2.5 Implement asset glob matching using `fnmatch`
- [ ] 1.3 Add TOML validation logic
  - [ ] 1.3.1 Validate mutually exclusive `ipa_url` and `repo_url` fields
  - [ ] 1.3.2 Validate required fields for GitHub release tasks
  - [ ] 1.3.3 Validate GitHub repo URL format
  - [ ] 1.3.4 Add clear error messages for validation failures

## 2. Device List Caching

- [ ] 2.1 Update `scripts/sync_profiles.rb` for device list caching
  - [ ] 2.1.1 Add method to save device list as JSON
  - [ ] 2.1.2 Add SHA-256 checksum generation for device list
  - [ ] 2.1.3 Create `device-list.json` with devices, timestamp, and checksum
  - [ ] 2.1.4 Write device list JSON to `work/cache/device-list.json`
- [ ] 2.2 Add device list comparison in `scripts/check_changes.py`
  - [ ] 2.2.1 Load cached `device-list.json` if exists
  - [ ] 2.2.2 Load current `device-list.json` from sync_profiles output
  - [ ] 2.2.3 Compare checksums to detect changes
  - [ ] 2.2.4 Set `rebuild_all` flag based on comparison
  - [ ] 2.2.5 Log device additions/removals if detected

## 3. GitHub Release Version Tracking

- [ ] 3.1 Implement release version fetching in `scripts/run_signing.py`
  - [ ] 3.1.1 Parse `repo_url` to extract owner and repo name
  - [ ] 3.1.2 Call GitHub API to fetch release data
  - [ ] 3.1.3 Filter assets by `release_glob` pattern
  - [ ] 3.1.4 Extract version tag, `published_at`, and download URL
  - [ ] 3.1.5 Handle missing releases or no matching assets gracefully
- [ ] 3.2 Implement version caching
  - [ ] 3.2.1 Load `release-versions.json` from cache directory
  - [ ] 3.2.2 Compare current release with cached version for each task
  - [ ] 3.2.3 Update cache with new version data after successful processing
  - [ ] 3.2.4 Write updated `release-versions.json` to `work/cache/`
- [ ] 3.3 Integrate version-based download
  - [ ] 3.3.1 Replace direct URL download with GitHub asset download for `repo_url` tasks
  - [ ] 3.3.2 Use asset download URL from GitHub API response
  - [ ] 3.3.3 Maintain backwards compatibility for `ipa_url` tasks

## 4. Workflow Changes

- [ ] 4.1 Configure GitHub Token for API access
  - [ ] 4.1.1 Add `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` to workflow env section
  - [ ] 4.1.2 Verify token has `contents: read` permission (default for workflows)
  - [ ] 4.1.3 Document that no additional secrets configuration is required
- [ ] 4.2 Add cache restore step to `.github/workflows/sign-and-upload.yml`
  - [ ] 4.2.1 Add `actions/cache/restore@v4` step before sync profiles
  - [ ] 4.2.2 Set cache key to `ci-cache-${{ github.run_id }}`
  - [ ] 4.2.3 Set restore-keys to `ci-cache-` for fallback
  - [ ] 4.2.4 Cache path: `work/cache/*.json`
- [ ] 4.3 Add change detection step
  - [ ] 4.3.1 Run `scripts/check_changes.py` after profile sync
  - [ ] 4.3.2 Output rebuild decisions to `$GITHUB_OUTPUT` or environment
  - [ ] 4.3.3 Parse output to set conditional variables
- [ ] 4.4 Add conditional profile sync logic
  - [ ] 4.4.1 Use `if: steps.check_changes.outputs.rebuild_all == 'true'` for sync step
  - [ ] 4.4.2 Skip profile sync when devices unchanged
  - [ ] 4.4.3 Reuse profiles from previous run (cached in `work/profiles/`)
- [ ] 4.5 Update signing script invocation
  - [ ] 4.5.1 Pass rebuild list to `run_signing.py` via environment variable or JSON file
  - [ ] 4.5.2 Modify `run_signing.py` to iterate only over rebuild list
  - [ ] 4.5.3 Log skipped tasks with reason
- [ ] 4.6 Add cache save step
  - [ ] 4.6.1 Add `actions/cache/save@v4` step at workflow end
  - [ ] 4.6.2 Save `work/cache/*.json` to cache
  - [ ] 4.6.3 Use `if: always()` to save even on partial failures
- [ ] 4.7 Add scheduled trigger
  - [ ] 4.7.1 Add `schedule: - cron: '0 2 * * *'` to workflow triggers
  - [ ] 4.7.2 Test scheduled workflow execution
- [ ] 4.8 Add force rebuild input
  - [ ] 4.8.1 Add `force_rebuild` boolean input to `workflow_dispatch`
  - [ ] 4.8.2 Pass input to change detection script
  - [ ] 4.8.3 Ignore cache when `force_rebuild` is true
- [ ] 4.9 Add workflow concurrency control
  - [ ] 4.9.1 Add concurrency group `sign-and-upload`
  - [ ] 4.9.2 Set `cancel-in-progress: false` to prevent race conditions

## 5. Testing and Validation

- [ ] 5.1 Unit test `scripts/check_changes.py`
  - [ ] 5.1.1 Test device list checksum calculation
  - [ ] 5.1.2 Test device list comparison (match, mismatch, missing cache)
  - [ ] 5.1.3 Test rebuild list generation logic
  - [ ] 5.1.4 Test force rebuild override
- [ ] 5.2 Unit test GitHub API integration
  - [ ] 5.2.1 Test release fetching (stable and prerelease)
  - [ ] 5.2.2 Test asset glob matching
  - [ ] 5.2.3 Test rate limit handling
  - [ ] 5.2.4 Test authentication header inclusion
- [ ] 5.3 Integration test workflow
  - [ ] 5.3.1 Test first run (no cache) - should full rebuild
  - [ ] 5.3.2 Test second run (no changes) - should skip all
  - [ ] 5.3.3 Test release version change - should rebuild only changed task
  - [ ] 5.3.4 Test device addition - should rebuild all
  - [ ] 5.3.5 Test manual force rebuild - should rebuild all
  - [ ] 5.3.6 Test scheduled run - should use cache and detect changes
- [ ] 5.4 Validate backwards compatibility
  - [ ] 5.4.1 Test existing `ipa_url` tasks continue working
  - [ ] 5.4.2 Test webhook trigger continues working
  - [ ] 5.4.3 Verify no breaking changes to TOML schema for existing configs

## 6. Documentation and Examples

- [ ] 6.1 Update `README.md`
  - [ ] 6.1.1 Document new `repo_url`, `release_glob`, `use_prerelease` fields
  - [ ] 6.1.2 Add examples for GitHub release tracking configuration
  - [ ] 6.1.3 Document cache behavior and limitations
  - [ ] 6.1.4 Document `force_rebuild` workflow input
  - [ ] 6.1.5 Document scheduled workflow execution
- [ ] 6.2 Update `configs/tasks.toml.example`
  - [ ] 6.2.1 Add example task with GitHub release tracking
  - [ ] 6.2.2 Add comments explaining new fields
  - [ ] 6.2.3 Show both `ipa_url` and `repo_url` examples
- [ ] 6.3 Add inline code comments
  - [ ] 6.3.1 Document cache file formats in code
  - [ ] 6.3.2 Document change detection algorithm
  - [ ] 6.3.3 Document GitHub API integration points

## 7. Deployment and Monitoring

- [ ] 7.1 Pre-deployment checklist
  - [ ] 7.1.1 Verify all tests pass
  - [ ] 7.1.2 Review code changes for security issues
  - [ ] 7.1.3 Ensure `GITHUB_TOKEN` is available in workflow
  - [ ] 7.1.4 Verify cache storage limits not exceeded
- [ ] 7.2 Deploy to production
  - [ ] 7.2.1 Merge changes to main branch
  - [ ] 7.2.2 Trigger manual workflow run to initialize cache
  - [ ] 7.2.3 Monitor first scheduled run
- [ ] 7.3 Post-deployment validation
  - [ ] 7.3.1 Verify cache files created correctly
  - [ ] 7.3.2 Check workflow logs for cache hit/miss rates
  - [ ] 7.3.3 Verify reduced runtime for unchanged tasks
  - [ ] 7.3.4 Monitor GitHub Actions minutes usage for cost savings
