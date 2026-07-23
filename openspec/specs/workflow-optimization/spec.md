# workflow-optimization Specification

## Purpose
TBD - created by archiving change add-ci-caching-optimization. Update Purpose after archive.
## Requirements
### Requirement: Change Detection Logic
The system SHALL determine rebuild work from complete source identity and cache evidence rather than source kind alone.

#### Scenario: Determine rebuild list
- **WHEN** the workflow starts after cache restoration
- **THEN** the system SHALL create a rebuild list of tasks requiring execution
- **AND** the list SHALL include tasks with release identity changes, direct URL or digest changes, new tasks, invalid cache evidence, or forced rebuild policy

#### Scenario: Skip unchanged GitHub release tasks
- **WHEN** a task uses GitHub release tracking
- **AND** the resolved release and asset identity match the complete cached fingerprint
- **AND** `rebuild_all` is false
- **THEN** the task SHALL be eligible for cache reuse through current prerequisite and full-artifact verification
- **AND** the system SHALL report that the source identity is unchanged

#### Scenario: Reuse an unchanged direct URL task
- **WHEN** a direct task's configured URL and `ipa_sha256` match the complete cached fingerprint
- **AND** `rebuild_all` is false
- **THEN** the task SHALL be eligible for the same guarded cache-reuse path as an unchanged GitHub source
- **AND** source kind alone SHALL NOT force a rebuild

#### Scenario: Direct URL identity changes
- **WHEN** either the configured direct URL or `ipa_sha256` differs from cached evidence
- **THEN** the system SHALL rebuild the task
- **AND** the rebuild reason SHALL identify source identity change without exposing credentials

#### Scenario: Always rebuild new tasks
- **WHEN** a task exists in `tasks.toml` but has no complete cache record
- **THEN** the system SHALL include the task in the rebuild list
- **AND** the system SHALL report that initial processing is required

#### Scenario: Operator forces a rebuild
- **WHEN** `rebuild_all` is true
- **THEN** every selected task SHALL rebuild regardless of otherwise reusable source or cache identity

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

- **WHEN** the workflow reaches its cache-finalization step, whether earlier task processing succeeded or failed
- **THEN** the system SHALL save both `release-versions.json` and `device-list.json` to cache
- **AND** the system SHALL use GitHub Actions cache save action
- **AND** release-version entries SHALL only reflect tasks that completed signing and upload successfully
- **AND** the cache SHALL be available for subsequent workflow runs

#### Scenario: Handle cache save failure

- **WHEN** cache save fails
- **THEN** the system SHALL log a warning
- **AND** the system SHALL not fail the workflow
- **AND** the next run SHALL perform a full rebuild due to cache miss
