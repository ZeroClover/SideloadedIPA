## MODIFIED Requirements

### Requirement: Isolated and atomic output production

The system SHALL sign a workspace copy and SHALL expose a result artifact only after backend completion and backend-evidence validation, with the run's complete independent verification remaining the gate for cache promotion and publication.

#### Scenario: Signing succeeds

- **WHEN** all nodes are signed and backend result evidence, including plan identity and per-node and output digests, validates
- **THEN** the IPA SHALL be atomically promoted to the task result path
- **AND** the original downloaded IPA SHALL remain unchanged
- **AND** cache promotion and publication SHALL remain gated on the run's complete verification pass

#### Scenario: Signing fails midway

- **WHEN** any nested or root signing operation fails
- **THEN** the incomplete workspace and temporary output SHALL be discarded
- **AND** a previous verified output SHALL not be overwritten
