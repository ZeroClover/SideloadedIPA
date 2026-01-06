# Task Configuration Specification

## ADDED Requirements

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

The system SHALL validate task configuration before execution to ensure data integrity.

#### Scenario: Validate mutually exclusive IPA source fields

- **WHEN** loading task configuration
- **THEN** each task SHALL have exactly one of `ipa_url` or `repo_url` defined
- **AND** tasks with both or neither SHALL be rejected with validation errors

#### Scenario: Validate required fields for GitHub release tasks

- **WHEN** a task defines `repo_url`
- **THEN** the task SHALL also have valid `task_name`, `app_name`, `bundle_id`, and `asset_server_path`
- **AND** `repo_url` SHALL be a valid GitHub repository URL format

#### Scenario: Validate required fields for direct URL tasks

- **WHEN** a task defines `ipa_url`
- **THEN** the task SHALL also have valid `task_name`, `app_name`, `bundle_id`, and `asset_server_path`
- **AND** `ipa_url` SHALL be a valid HTTP/HTTPS URL

### Requirement: Backwards Compatibility

The system SHALL maintain full backwards compatibility with existing `ipa_url` configurations.

#### Scenario: Legacy configuration continues working

- **WHEN** an existing task configuration uses only `ipa_url`
- **THEN** the system SHALL process the task using direct IPA download
- **AND** no changes to existing configurations SHALL be required
