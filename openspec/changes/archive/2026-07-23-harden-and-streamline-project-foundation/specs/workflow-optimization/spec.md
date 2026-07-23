## MODIFIED Requirements

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
