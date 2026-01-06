# Workflow Optimization Specification

## ADDED Requirements

### Requirement: Change Detection Logic

The system SHALL implement intelligent change detection to determine which tasks require execution.

#### Scenario: Determine rebuild list

- **WHEN** the workflow starts after cache restoration
- **THEN** the system SHALL create a rebuild list of tasks requiring execution
- **AND** the list SHALL include tasks with version changes, new tasks, or direct URL tasks
- **AND** if `rebuild_all` is true, the list SHALL include all tasks

#### Scenario: Skip unchanged GitHub release tasks

- **WHEN** a task uses GitHub release tracking
- **AND** the cached version matches the current release version and timestamp
- **AND** `rebuild_all` is false
- **THEN** the system SHALL exclude the task from the rebuild list
- **AND** the system SHALL log that the task is skipped (already up to date)

#### Scenario: Always rebuild direct URL tasks

- **WHEN** a task uses `ipa_url` instead of `repo_url`
- **THEN** the system SHALL always include the task in the rebuild list
- **AND** the system SHALL log that direct URL tasks cannot be version-tracked

#### Scenario: Always rebuild new tasks

- **WHEN** a task exists in `tasks.toml` but not in the version cache
- **THEN** the system SHALL include the task in the rebuild list
- **AND** the system SHALL log that this is a new task requiring initial processing

### Requirement: Conditional Execution

The system SHALL execute signing and upload steps only for tasks in the rebuild list.

#### Scenario: Process only tasks in rebuild list

- **WHEN** executing the signing workflow
- **THEN** the system SHALL iterate only over tasks in the rebuild list
- **AND** the system SHALL skip tasks not in the rebuild list
- **AND** the system SHALL log the count of processed vs skipped tasks

#### Scenario: Log execution summary

- **WHEN** the workflow completes
- **THEN** the system SHALL log the total number of tasks
- **AND** the system SHALL log the number of tasks rebuilt
- **AND** the system SHALL log the number of tasks skipped
- **AND** the system SHALL log the reason for rebuild (device change, version change, new task, etc.)

### Requirement: Cache State Management

The system SHALL maintain and update cache state files throughout the workflow.

#### Scenario: Restore cache at workflow start

- **WHEN** the workflow starts
- **THEN** the system SHALL restore both `release-versions.json` and `device-list.json` from cache
- **AND** the system SHALL use GitHub Actions cache restore action
- **AND** the system SHALL handle cache miss gracefully

#### Scenario: Update release version cache

- **WHEN** a task with GitHub release tracking is successfully processed
- **THEN** the system SHALL update `release-versions.json` with the new version, timestamp, and download URL
- **AND** the system SHALL preserve entries for other tasks
- **AND** the system SHALL update the `last_updated` timestamp

#### Scenario: Save cache at workflow end

- **WHEN** the workflow completes successfully
- **THEN** the system SHALL save both `release-versions.json` and `device-list.json` to cache
- **AND** the system SHALL use GitHub Actions cache save action
- **AND** the cache SHALL be available for subsequent workflow runs

#### Scenario: Handle cache save failure

- **WHEN** cache save fails
- **THEN** the system SHALL log a warning
- **AND** the system SHALL not fail the workflow
- **AND** the next run SHALL perform a full rebuild due to cache miss
