# Scheduled Execution Specification

## ADDED Requirements

### Requirement: Daily Scheduled Workflow

The system SHALL execute the signing workflow on a daily schedule to ensure cache freshness and automatic release processing.

#### Scenario: Configure daily cron schedule

- **WHEN** the workflow is configured
- **THEN** the workflow SHALL include a `schedule` trigger with cron expression `0 2 * * *`
- **AND** the schedule SHALL run daily at 02:00 UTC
- **AND** the schedule SHALL run independently of manual or webhook triggers

#### Scenario: Execute scheduled run with cache

- **WHEN** the scheduled workflow runs
- **THEN** the system SHALL restore cache from previous runs
- **AND** the system SHALL perform change detection as normal
- **AND** the system SHALL only rebuild tasks with detected changes

#### Scenario: Prevent cache expiration

- **WHEN** the scheduled workflow runs successfully
- **THEN** the system SHALL update the cache with current state
- **AND** the updated cache SHALL reset the 7-day expiration timer
- **AND** the cache SHALL remain available for future runs

### Requirement: Manual Force Rebuild

The system SHALL support forcing a full rebuild via manual workflow dispatch, ignoring cached state.

#### Scenario: Add force rebuild input parameter

- **WHEN** the workflow is manually triggered via `workflow_dispatch`
- **THEN** the workflow SHALL accept a `force_rebuild` boolean input parameter
- **AND** the parameter SHALL default to `false`
- **AND** the parameter description SHALL clearly indicate it ignores cache

#### Scenario: Execute force rebuild

- **WHEN** `force_rebuild` input is `true`
- **THEN** the system SHALL ignore all cached state (version cache and device cache)
- **AND** the system SHALL set `rebuild_all` to `true`
- **AND** the system SHALL regenerate all profiles and rebuild all IPAs
- **AND** the system SHALL update cache with fresh state after completion

#### Scenario: Normal manual run respects cache

- **WHEN** `force_rebuild` input is `false` or omitted
- **THEN** the workflow SHALL use cached state as normal
- **AND** the workflow SHALL perform change detection
- **AND** the workflow SHALL only rebuild changed tasks

### Requirement: Webhook Trigger Compatibility

The system SHALL maintain compatibility with existing `repository_dispatch` webhook triggers.

#### Scenario: Webhook trigger uses cache

- **WHEN** the workflow is triggered via `repository_dispatch` with type `sign_ipas`
- **THEN** the workflow SHALL restore cache and perform change detection
- **AND** the workflow SHALL only rebuild changed tasks
- **AND** the workflow SHALL not support force rebuild parameter (no inputs in repository_dispatch)

#### Scenario: Webhook trigger behavior unchanged

- **WHEN** external systems trigger the workflow via webhook
- **THEN** the behavior SHALL be identical to manual runs without force_rebuild
- **AND** existing webhook integrations SHALL require no changes
- **AND** the workflow SHALL remain backwards compatible
