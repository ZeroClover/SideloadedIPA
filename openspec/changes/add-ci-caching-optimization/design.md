# Design: CI Caching and Smart Execution Optimization

## Context

The current CI workflow processes all signing tasks on every run without checking if the source applications or device configurations have changed. This design introduces intelligent caching and change detection to minimize unnecessary work.

### Constraints

- GitHub Actions cache has a 10GB total limit per repository
- Cache entries expire after 7 days of no access
- GitHub API rate limits: 60 requests/hour (unauthenticated), 5000/hour (authenticated)
- App Store Connect API has undocumented rate limits
- Workflow must remain backwards compatible with existing `ipa_url` configurations

### Stakeholders

- CI execution cost/time
- Cache storage limits
- API rate limits

## Goals / Non-Goals

### Goals

- Reduce unnecessary CI runs by caching release versions and device lists
- Support GitHub releases as IPA sources with automatic version tracking
- Execute only the minimum required work based on change detection
- Maintain backwards compatibility with existing TOML configurations
- Refresh cache daily via scheduled runs

### Non-Goals

- Support for non-GitHub release sources (GitLab, Bitbucket, etc.)
- Automatic rollback on signing failures
- Multi-architecture IPA support
- Advanced release selection (by tag pattern beyond prerelease flag)

## Decisions

### Decision 1: Cache Storage Format

**Choice**: Use two separate JSON files stored in GitHub Actions cache

**Structure**:
```json
// release-versions.json
{
  "tasks": {
    "task_name": {
      "version": "v1.2.3",
      "published_at": "2025-01-06T12:00:00Z",
      "download_url": "https://github.com/owner/repo/releases/download/v1.2.3/app.ipa",
      "asset_id": 12345678
    }
  },
  "last_updated": "2025-01-06T12:00:00Z"
}

// device-list.json
{
  "devices": [
    {
      "id": "DEVICE_ID",
      "name": "iPhone 15 Pro",
      "platform": "IOS",
      "device_class": "IPHONE",
      "udid": "00000000-0000000000000000",
      "status": "ENABLED"
    }
  ],
  "last_updated": "2025-01-06T12:00:00Z",
  "checksum": "sha256:abcdef..."
}
```

**Rationale**:
- Separate files allow independent cache invalidation
- JSON is easily parsable in both Python and Ruby
- Includes metadata for debugging (last_updated)
- Checksum allows quick comparison without deep equality checks

**Alternatives considered**:
- Single combined cache file: Harder to invalidate independently
- TOML format: Less standardized for nested structures in Python
- Database (SQLite): Overkill for simple key-value storage

### Decision 2: GitHub API Authentication

**Choice**: Use `GITHUB_TOKEN` (automatic token provided by Actions) for all GitHub API requests

**Rationale**:
- **Automatic availability**: Provided by default in GitHub Actions via `secrets.GITHUB_TOKEN`
- **Higher rate limits**: 1,000 requests/hour per repository (vs 60 requests/hour for unauthenticated)
- **Avoids shared IP throttling**: GitHub-hosted runners share IP addresses across multiple repositories
  - Unauthenticated requests are rate-limited per IP address (60/hour total)
  - Multiple workflows on the same runner IP would quickly exhaust the shared quota
  - Authenticated requests are rate-limited per repository, isolated from other workflows
- **No secret management**: No additional configuration or token rotation required
- **Appropriate permissions**: Scoped to repository access automatically
- **Better reliability**: Reduces risk of random rate limit failures from concurrent workflows

**Shared IP Problem**:
GitHub-hosted runners (especially macOS runners) often share public IP addresses across many repositories. Without authentication:
1. Workflow A (our repo) makes unauthenticated API calls → consumes shared IP quota
2. Workflow B (different repo, same runner IP) makes calls → further depletes quota
3. Our workflow hits rate limit despite making only a few requests
4. Result: Random, unpredictable failures unrelated to our actual usage

Using `GITHUB_TOKEN` eliminates this issue entirely by moving to per-repository rate limiting.

**Implementation**:
- Pass token via `Authorization: Bearer $GITHUB_TOKEN` header
- Include in workflow environment via `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`
- Monitor rate limit headers: `X-RateLimit-Remaining`, `X-RateLimit-Reset`

**Alternatives considered**:
- **Personal Access Token (PAT)**:
  - Pros: Higher rate limit (5,000/hour)
  - Cons: Requires secret management, token rotation, broader permissions scope
  - Decision: Unnecessary complexity for our needs
- **Unauthenticated requests**:
  - Pros: No token required
  - Cons: Only 60 requests/hour per IP, vulnerable to shared IP exhaustion
  - Decision: Too restrictive and unreliable for production use

### Decision 3: Change Detection Logic

**Execution flow**:

```
1. Restore cache (release-versions.json, device-list.json)
2. Fetch current device list from ASC API
3. Compare with cached device-list.json:
   - If different → Set rebuild_all=true
   - If same → Set rebuild_all=false
4. For each task in tasks.toml:
   - If has repo_url:
     - Fetch latest release via GitHub API
     - Compare with cached version:
       - If different or missing → Add to rebuild_list
   - If has ipa_url or missing from cache:
     - Add to rebuild_list (always rebuild)
5. Execute workflows:
   - If rebuild_all=true:
     - Regenerate all profiles
     - Process all tasks
   - Else:
     - Skip profile sync (reuse existing)
     - Process only tasks in rebuild_list
6. Update cache with new state
```

**Rationale**:
- Device changes affect all apps (profiles contain device UDIDs)
- Release version changes only affect individual apps
- New tasks or ipa_url tasks always rebuild (no version tracking)
- Profile reuse is safe when device list hasn't changed

### Decision 4: TOML Configuration Schema

**New fields** (mutually exclusive with `ipa_url`):

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My App"
bundle_id = "com.example.app"

# Option 1: Direct IPA URL (existing, unchanged)
ipa_url = "https://example.com/app.ipa"

# Option 2: GitHub Release tracking (new)
repo_url = "https://github.com/owner/repo"  # Required
release_glob = "*.ipa"                      # Optional, default: "*.ipa"
use_prerelease = false                       # Optional, default: false

asset_server_path = "/path/to/upload/"
```

**Validation rules**:
- Either `ipa_url` OR `repo_url` must be present (not both)
- If `repo_url` is present, `release_glob` and `use_prerelease` are optional
- `release_glob` follows Python `fnmatch` pattern syntax

**Rationale**:
- Backwards compatible: existing configs with `ipa_url` continue working
- Clear separation: either tracked release or direct URL
- Flexible: glob pattern allows matching specific file types or names

### Decision 5: Scheduled Workflow Trigger

**Choice**: Use `schedule` with daily cron at 02:00 UTC

```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 02:00 UTC
  workflow_dispatch:
    inputs:
      force_rebuild:
        description: 'Force full rebuild (ignore cache)'
        type: boolean
        default: false
  repository_dispatch:
    types: [sign_ipas]
```

**Rationale**:
- Daily refresh ensures cache doesn't expire (7-day limit)
- 02:00 UTC avoids peak usage times
- Manual runs can force rebuild via input parameter
- Webhook triggers remain unchanged

**Alternatives considered**:
- Hourly: Too frequent, wastes Actions minutes
- Weekly: Risk of cache expiration if no manual runs
- On push: Not applicable (no code changes trigger signing)

## Risks / Trade-offs

### Risk: GitHub API Rate Limits

**Scenario**: Many tasks with `repo_url` could exceed 1000 requests/hour

**Mitigation**:
- Batch API requests where possible
- Cache release data for subsequent task processing
- Monitor rate limit headers and fail gracefully
- Consider adding `GITHUB_TOKEN` with higher limits if needed

### Risk: Cache Expiration

**Scenario**: No CI runs for 7 days causes cache loss, triggering full rebuild

**Mitigation**:
- Daily scheduled run ensures cache refresh
- Full rebuild on cache miss is expected behavior
- Log cache hit/miss for monitoring

### Risk: Device List API Failures

**Scenario**: ASC API temporarily unavailable during device list fetch

**Mitigation**:
- Use cached device list as fallback (with warning)
- Retry logic with exponential backoff
- Fail workflow if no cache exists and API fails

### Risk: Race Conditions

**Scenario**: Multiple concurrent workflows could corrupt cache

**Mitigation**:
- GitHub Actions cache is eventually consistent
- Concurrent writes will result in last-write-wins
- Schedule + webhook triggers unlikely to overlap
- Add workflow concurrency control:
  ```yaml
  concurrency:
    group: sign-and-upload
    cancel-in-progress: false
  ```

## Migration Plan

### Phase 1: Implementation

1. Add cache restore/save steps to workflow
2. Implement `scripts/check_changes.py` for change detection
3. Update `scripts/run_signing.py` with GitHub API integration
4. Update `scripts/sync_profiles.rb` with device comparison logic
5. Add workflow conditional execution logic

### Phase 2: Deployment

1. Merge to main branch
2. First manual run will trigger full rebuild (no cache)
3. Subsequent runs will use cache
4. Monitor logs for cache hit rates

### Phase 3: Validation

1. Verify cache files are created correctly
2. Test release version change detection
3. Test device list change detection
4. Test scheduled workflow execution
5. Verify backwards compatibility with `ipa_url` tasks

### Rollback Plan

If issues arise:
1. Revert workflow changes to remove caching logic
2. System falls back to full rebuild on every run (original behavior)
3. No data loss risk (cache is additive optimization)

## Open Questions

1. **Should we cache provisioning profiles themselves?**
   - Pros: Faster profile setup
   - Cons: Profiles may become stale if devices/certs change externally
   - **Decision**: No, regenerate profiles when device list changes (safer)

2. **Should we support version pinning (e.g., specific release tag)?**
   - Current design always uses latest/latest-prerelease
   - **Decision**: Defer to future iteration if requested

3. **Should we notify on new release detection?**
   - Could integrate with Slack/Discord webhooks
   - **Decision**: Out of scope, use existing webhook notification
