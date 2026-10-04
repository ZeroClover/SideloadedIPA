# scheduled-execution Specification

## Purpose
Define scheduled and manual pipeline execution and its operational trigger contract.

## Requirements

### Requirement: Daily Scheduled Workflow
The signing workflow SHALL run daily at 02:00 UTC and process publication-enabled tasks through the current resource, fingerprint, verification and publication gates.

#### Scenario: Configure daily schedule
- **WHEN** the workflow is configured
- **THEN** it SHALL use schedule cron 0 2 * * * independently of manual or webhook triggers

#### Scenario: Scheduled run with cache
- **WHEN** the scheduled workflow runs
- **THEN** it SHALL restore available signing cache and validate current prerequisites
- **AND** only tasks requiring signing work SHALL invoke the signing backend
- **AND** verified cache-hit tasks SHALL still publish, revalidate and progress eligible retirement

#### Scenario: Persist a successful run
- **WHEN** the job succeeds
- **THEN** its reviewed durable cache SHALL be saved for future runs
- **AND** external cache retention/eviction SHALL NOT be treated as a correctness guarantee

### Requirement: Manual Force Rebuild
The workflow SHALL support force_rebuild as an optional boolean workflow_dispatch input defaulting to false, which bypasses signing reuse without bypassing current prerequisite or verification gates.

#### Scenario: Force rebuild requested
- **WHEN** the manual input is true
- **THEN** the signing command SHALL receive --force-rebuild and rebuild selected production tasks
- **AND** valid existing profiles MAY still be reused after normal reconciliation
- **AND** forcing signing SHALL NOT itself require recreating every profile or selecting an older source release

#### Scenario: Normal manual run
- **WHEN** force_rebuild is false or absent
- **THEN** complete current signing inputs SHALL determine reuse versus rebuild
- **AND** independent verification and authorized publication SHALL run for selected tasks

### Requirement: Repository dispatch execution
The workflow SHALL support repository_dispatch type sign_ipas through the same current production gates as a normal non-forced manual run.

#### Scenario: Receive supported dispatch
- **WHEN** a sign_ipas repository dispatch triggers execution
- **THEN** the workflow SHALL restore available signing cache and evaluate complete current fingerprints
- **AND** it SHALL independently verify and publish selected tasks, including valid cache hits
- **AND** it SHALL NOT infer a force-rebuild request from a manual input absent from this event
