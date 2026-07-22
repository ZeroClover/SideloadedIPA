## ADDED Requirements

### Requirement: Fail-closed publication enablement

A task SHALL be publishable only when its configuration explicitly enables publication.

#### Scenario: Task omits the publication flag

- **WHEN** a task entry does not declare `publication_enabled`
- **THEN** configuration SHALL treat the task as non-publishing
- **AND** enabling publication SHALL require an explicit reviewed configuration edit

#### Scenario: Production tasks declare the flag explicitly

- **WHEN** production task configuration is validated
- **THEN** every production task SHALL declare `publication_enabled` explicitly
- **AND** the default SHALL never decide whether a production task publishes

#### Scenario: Default becomes fail-closed

- **WHEN** the fail-closed default is introduced
- **THEN** every currently publishing production task SHALL receive an explicit `publication_enabled = true` entry in the same change
- **AND** published artifact identity and registry behavior SHALL remain unchanged
