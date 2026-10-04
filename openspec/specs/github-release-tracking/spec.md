# github-release-tracking Specification

## Purpose
Define deterministic GitHub release/asset selection, bounded transport and source evidence verification.
## Requirements
### Requirement: GitHub API Integration
The system SHALL fetch release evidence through bounded GitHub REST reads and deterministic selection of one matching asset.

#### Scenario: Latest stable release
- **WHEN** use_prerelease is false
- **THEN** it SHALL request the repository's releases/latest endpoint and validate its release object

#### Scenario: Prerelease selection
- **WHEN** use_prerelease is true
- **THEN** it SHALL request the first releases page with per_page=100
- **AND** choose the first non-draft prerelease in API order, or the first non-draft release if no prerelease is present
- **AND** an empty or malformed selection SHALL fail explicitly

#### Scenario: GitHub read fails
- **WHEN** transport, HTTP, oversized response or JSON decoding prevents release evidence from being read
- **THEN** intake SHALL fail with a redacted adapter diagnostic before signing or publication
- **AND** HTTP error bodies SHALL be closed
- **AND** failure SHALL NOT be described as a successful unchanged-release cache hit

### Requirement: Authenticated API Access
The production workflow SHALL provide its repository token for GitHub release reads; the reusable source adapter SHALL attach Bearer authentication when a token is supplied without exposing it in retained evidence.

#### Scenario: Production workflow supplies credentials
- **WHEN** a production stage reads a GitHub source
- **THEN** its environment SHALL receive GITHUB_TOKEN from the workflow secret
- **AND** authentication SHALL use a request header rather than a URL

#### Scenario: Local read without token
- **WHEN** a local caller omits a token
- **THEN** the adapter SHALL perform the supported unauthenticated read subject to GitHub access and rate limits
- **AND** it SHALL NOT claim a fixed authenticated quota or proactively reject the caller merely because a token is absent

### Requirement: Asset Matching and Download

The system SHALL locate exactly one IPA file from GitHub release assets using the task's glob pattern and SHALL reject ambiguous source selection.

#### Scenario: Match exactly one asset by glob pattern

- **WHEN** the system evaluates a release for a GitHub-backed task
- **THEN** it SHALL filter all release assets by the `release_glob` pattern using fnmatch
- **AND** when exactly one asset matches, it SHALL select that asset and record its asset ID, name, URL, size, and available digest as source evidence

#### Scenario: No asset matches

- **WHEN** no release asset matches the `release_glob` pattern
- **THEN** source selection SHALL fail before download or signing
- **AND** the diagnostic SHALL include the pattern and available asset names

#### Scenario: Multiple assets match

- **WHEN** multiple release assets match the `release_glob` pattern
- **THEN** source selection SHALL fail instead of selecting the first match
- **AND** the diagnostic SHALL list every matching asset name and require a more specific selector

#### Scenario: Download the unambiguous matched asset

- **WHEN** exactly one matching asset has been selected
- **THEN** the system SHALL download that asset from its `browser_download_url`
- **AND** the system SHALL verify that the download completed successfully
- **AND** the system SHALL use only that downloaded file for signing

### Requirement: Version Comparison
The system SHALL bind release tag, publication timestamp and selected asset evidence into the complete signing fingerprint; version equality alone SHALL NOT authorize skipping correctness or publication stages.

#### Scenario: Release or asset identity changes
- **WHEN** release tag, published_at, selected asset identity, URL or downloaded bytes changes the complete fingerprint
- **THEN** the affected selected task SHALL rebuild from current validated inputs

#### Scenario: Complete signing identity unchanged
- **WHEN** release and asset evidence match within the complete verified signing fingerprint
- **THEN** the task MAY reuse signing output through current-prerequisite and independent artifact verification
- **AND** authorized publication and retirement progression SHALL still execute

#### Scenario: No complete cache record
- **WHEN** the selected task lacks a complete signing-cache record
- **THEN** it SHALL require initial signing and independent verification
- **AND** a standalone release-version entry SHALL NOT replace that evidence

### Requirement: Bounded HTTPS asset transport
The system MUST download a selected GitHub release asset over HTTPS within a package-owned resource policy before inventory or signing can begin.

#### Scenario: Selected asset uses HTTPS
- **WHEN** GitHub returns the selected asset download URL
- **THEN** the downloader SHALL require an HTTPS URL with a valid authority
- **AND** redirects SHALL NOT downgrade the transfer to an insecure scheme

#### Scenario: Declared content length exceeds the limit
- **WHEN** the HTTP response declares a byte length greater than the reviewed source limit
- **THEN** the download SHALL fail before the response body is written
- **AND** the diagnostic SHALL report declared and allowed bytes

#### Scenario: Stream exceeds the limit without a usable length
- **WHEN** the streamed bytes exceed the reviewed source limit
- **THEN** the downloader SHALL stop immediately
- **AND** the temporary file SHALL be removed without exposing a source artifact

### Requirement: Release asset evidence verification
The system SHALL reconcile the downloaded bytes with the selected GitHub asset's advertised identity, size, and available digest.

#### Scenario: Advertised size matches
- **WHEN** an asset download completes
- **THEN** its actual byte count SHALL equal the selected asset's advertised size
- **AND** both values SHALL be retained as source evidence

#### Scenario: Advertised size differs
- **WHEN** the actual byte count differs from the selected asset's advertised size
- **THEN** source intake SHALL fail before inventory
- **AND** no cache-success or publication state SHALL change

#### Scenario: GitHub advertises a SHA-256 digest
- **WHEN** the selected asset includes digest evidence
- **THEN** the downloaded SHA-256 SHALL match it exactly
- **AND** a mismatch SHALL fail closed

#### Scenario: GitHub does not advertise a digest
- **WHEN** the selected asset has no digest field
- **THEN** the system SHALL still calculate and retain the actual SHA-256 with the asset ID, URL, and size
- **AND** downstream stages SHALL bind to that measured digest for the current run

### Requirement: Identity-preserving source retries
The system SHALL retry a transient asset read only within a bounded policy and only while the resolved release and asset identity remain unchanged.

#### Scenario: Idempotent download fails transiently
- **WHEN** the same selected asset encounters a retryable transport or server failure before successful completion
- **THEN** the downloader SHALL use bounded backoff and a fresh temporary file
- **AND** every attempt SHALL retain the same release tag, asset ID, URL, expected size, and expected digest

#### Scenario: Asset identity changes during retry
- **WHEN** a retry would require resolving a different release, asset ID, URL, size, or digest
- **THEN** the current operation SHALL fail
- **AND** a new inspect run SHALL be required

#### Scenario: Retry budget is exhausted
- **WHEN** all permitted attempts fail
- **THEN** source intake SHALL return one bounded diagnostic with the attempt count
- **AND** no partial source file SHALL remain
