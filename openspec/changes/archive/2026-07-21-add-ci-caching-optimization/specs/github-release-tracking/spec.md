# GitHub Release Tracking Specification

## ADDED Requirements

### Requirement: GitHub API Integration

The system SHALL integrate with GitHub API to fetch release information and download IPA assets.

#### Scenario: Fetch latest stable release

- **WHEN** a task uses GitHub release tracking with `use_prerelease` set to `false`
- **THEN** the system SHALL call the GitHub API `/repos/{owner}/{repo}/releases/latest` endpoint
- **AND** the system SHALL extract release version, published timestamp, and asset download URLs

#### Scenario: Fetch latest prerelease

- **WHEN** a task uses GitHub release tracking with `use_prerelease` set to `true`
- **THEN** the system SHALL call the GitHub API `/repos/{owner}/{repo}/releases` endpoint
- **AND** the system SHALL select the most recent release where `prerelease` is `true`
- **AND** if no prerelease exists, the system SHALL fall back to the latest stable release

#### Scenario: Authenticate with GitHub token

- **WHEN** making GitHub API requests
- **THEN** the system SHALL use the `GITHUB_TOKEN` environment variable for authentication
- **AND** the system SHALL include the token in the `Authorization: Bearer` header
- **AND** the system SHALL respect GitHub API rate limit headers

#### Scenario: Handle GitHub API rate limits

- **WHEN** GitHub API returns a 403 with rate limit exceeded
- **THEN** the system SHALL log the rate limit reset time
- **AND** the system SHALL fail the workflow with a clear error message
- **AND** the system SHALL suggest checking rate limit status

### Requirement: Authenticated API Access

The system SHALL use authenticated GitHub API access to leverage GitHub Actions built-in token advantages and avoid shared IP rate limiting.

#### Scenario: Use GitHub Actions built-in token

- **WHEN** the workflow runs in GitHub Actions environment
- **THEN** the system SHALL use the automatically provided `GITHUB_TOKEN` secret
- **AND** the system SHALL NOT require manual token configuration
- **AND** the token SHALL be available via `secrets.GITHUB_TOKEN` in the workflow

#### Scenario: Achieve higher rate limits through authentication

- **WHEN** making authenticated API requests with `GITHUB_TOKEN`
- **THEN** the system SHALL benefit from authenticated rate limit of 1,000 requests per hour per repository
- **AND** the system SHALL avoid unauthenticated rate limit of 60 requests per hour per IP
- **AND** the system SHALL log current rate limit status in debug mode

#### Scenario: Avoid shared runner IP rate limiting

- **WHEN** multiple workflows run on GitHub-hosted runners with shared IP addresses
- **THEN** authenticated requests SHALL be counted separately per repository
- **AND** the system SHALL NOT be affected by rate limits from other repositories on the same runner IP
- **AND** the system SHALL NOT exhaust the shared IP pool's unauthenticated quota

#### Scenario: Verify token availability

- **WHEN** the workflow starts GitHub API integration
- **THEN** the system SHALL verify `GITHUB_TOKEN` is present in environment
- **AND** if the token is missing, the system SHALL fail with a clear error message
- **AND** the error message SHALL indicate workflow permissions may need adjustment

### Requirement: Asset Matching and Download

The system SHALL locate and download IPA files from GitHub release assets using glob patterns.

#### Scenario: Match asset by glob pattern

- **WHEN** a release has multiple assets
- **THEN** the system SHALL filter assets by the `release_glob` pattern using fnmatch
- **AND** the system SHALL select the first matching asset
- **AND** if no assets match, the system SHALL fail with an error

#### Scenario: Download matched asset

- **WHEN** a matching asset is found
- **THEN** the system SHALL download the asset from the `browser_download_url`
- **AND** the system SHALL verify the download completed successfully
- **AND** the system SHALL use the downloaded file for signing

#### Scenario: Multiple matching assets

- **WHEN** multiple assets match the glob pattern
- **THEN** the system SHALL use the first matching asset in the asset list
- **AND** the system SHALL log a warning listing all matched assets

### Requirement: Version Comparison

The system SHALL compare cached release versions with current versions to detect updates.

#### Scenario: Detect version change by tag

- **WHEN** the cached version tag differs from the current release tag
- **THEN** the system SHALL mark the task for rebuild
- **AND** the system SHALL log the version change (old → new)

#### Scenario: Detect version change by publish timestamp

- **WHEN** the release tag is the same but `published_at` timestamp differs
- **THEN** the system SHALL mark the task for rebuild
- **AND** the system SHALL log that the release was republished

#### Scenario: No version change detected

- **WHEN** both tag and `published_at` match the cached values
- **THEN** the system SHALL skip the task (no rebuild needed)
- **AND** the system SHALL log that the version is up to date

#### Scenario: Missing cached version

- **WHEN** no cached version exists for a task
- **THEN** the system SHALL mark the task for rebuild
- **AND** the system SHALL log that this is the first run for the task
