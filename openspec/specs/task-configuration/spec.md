# task-configuration Specification

## Purpose
TBD - created by archiving change add-ci-caching-optimization. Update Purpose after archive.
## Requirements
### Requirement: GitHub Release Source Configuration

The system SHALL support configuring tasks to automatically track GitHub releases as IPA sources instead of direct URLs.

#### Scenario: Configure task with GitHub release tracking

- **WHEN** a task is defined with `repo_url`, `release_glob`, and `use_prerelease` fields
- **THEN** the system SHALL fetch the latest release from the specified repository
- **AND** the system SHALL download the IPA asset matching the glob pattern

#### Scenario: GitHub release configuration conflicts with direct URL

- **WHEN** a task defines both `ipa_url` and `repo_url`
- **THEN** the configuration validation SHALL fail with a clear error message
- **AND** the workflow SHALL not proceed

#### Scenario: Default glob pattern for release assets

- **WHEN** a task defines `repo_url` without specifying `release_glob`
- **THEN** the system SHALL use `*.ipa` as the default glob pattern
- **AND** the system SHALL match any IPA file in the release assets

#### Scenario: Prerelease version selection

- **WHEN** a task defines `use_prerelease` as `true`
- **THEN** the system SHALL fetch the latest prerelease version
- **AND** the system SHALL fall back to the latest stable release if no prerelease exists

#### Scenario: Stable release version selection

- **WHEN** a task defines `use_prerelease` as `false` or omits the field
- **THEN** the system SHALL fetch the latest stable (non-prerelease) release only
- **AND** the system SHALL ignore any prerelease versions

### Requirement: Configuration Validation
The system SHALL validate task configuration before execution to ensure data integrity and secure remote source transport.

#### Scenario: Validate mutually exclusive IPA source fields
- **WHEN** loading task configuration
- **THEN** each task SHALL have exactly one of `ipa_url` or `repo_url` defined
- **AND** tasks with both or neither SHALL be rejected with validation errors

#### Scenario: Validate required fields for GitHub release tasks
- **WHEN** a task defines `repo_url`
- **THEN** the task SHALL also have valid `task_name`, `app_name`, and `bundle_id`
- **AND** `repo_url` SHALL be a valid GitHub repository identifier

#### Scenario: Validate required fields for direct URL tasks
- **WHEN** a task defines `ipa_url`
- **THEN** the task SHALL also have valid `task_name`, `app_name`, `bundle_id`, and `ipa_sha256`
- **AND** `ipa_url` SHALL be a valid HTTPS URL

#### Scenario: Reject insecure direct source transport
- **WHEN** a direct source URL uses HTTP or another unsupported scheme
- **THEN** configuration validation SHALL fail before download
- **AND** tests requiring an HTTP server SHALL opt into an explicit test-only transport dependency rather than production configuration

### Requirement: Immutable direct IPA source identity
Every direct IPA URL task MUST declare the reviewed SHA-256 digest of the exact source bytes.

#### Scenario: Configure a direct IPA source
- **WHEN** a task declares `ipa_url`
- **THEN** it SHALL also declare a canonical 64-character `ipa_sha256`
- **AND** URL plus digest SHALL form the reviewed direct-source identity

#### Scenario: Direct IPA digest is missing or malformed
- **WHEN** a direct URL task omits `ipa_sha256` or declares a non-SHA-256 value
- **THEN** configuration validation SHALL fail before network access
- **AND** the diagnostic SHALL identify the field and checksum migration command

#### Scenario: GitHub source declares a direct-source digest
- **WHEN** a task using `repo_url` also declares `ipa_sha256`
- **THEN** configuration validation SHALL fail
- **AND** GitHub asset digest evidence SHALL remain owned by release resolution
