## MODIFIED Requirements

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
