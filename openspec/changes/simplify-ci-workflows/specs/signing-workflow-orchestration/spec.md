## MODIFIED Requirements

### Requirement: Ordered staged workflow
The system SHALL coordinate source resolution, safe inventory, configuration matching, Apple resource planning/apply, signing-plan validation, signing, output verification, and publication as explicit ordered stages in production.

#### Scenario: Run a task successfully
- **WHEN** every stage completes successfully and no manual prerequisite remains
- **THEN** each downstream stage SHALL consume the typed manifest from its predecessor
- **AND** publication SHALL occur only after verification

#### Scenario: An early stage fails
- **WHEN** any production stage fails
- **THEN** all later mutation and publication stages SHALL be skipped
- **AND** production failure-injection tests SHALL prove that later adapters and side effects are not invoked
- **AND** the final report SHALL identify the first blocking stage and all available validation findings

### Requirement: Production acceptance for new multi-bundle tasks
The system SHALL allow a reviewed new multi-bundle task to use the verified production publication path as its end-to-end acceptance environment.

#### Scenario: Operator enables a new task for publication
- **WHEN** a reviewed task configuration explicitly enables publication
- **THEN** the normal production run SHALL inspect, plan/apply, sign, independently verify, publish, update the registry, revalidate the ITMS service, and retain redacted evidence
- **AND** a separate private non-publishing canary SHALL NOT be required

#### Scenario: New task fails before registry promotion
- **WHEN** source inspection, Apple reconciliation, signing, verification, upload confirmation, or batch publication fails
- **THEN** the task SHALL NOT be advertised by the production registry
- **AND** existing published task entries and referenced objects SHALL remain protected by the atomic publication and compensation rules

#### Scenario: Acceptance-relevant contract changes
- **WHEN** the selected source asset, bundle graph, signing policy, or entitlement contract changes
- **THEN** the normal fingerprint and verification gates SHALL decide rebuild and publication eligibility
- **AND** successful public ITMS installation MAY be used as the operator's device-level test

### Requirement: Debug sessions use least-privilege credentials
An SSH debug step MUST NOT inherit production signing, Apple API, object-storage, revalidation, or repository credentials unless a separately reviewed debug operation explicitly requires an individual credential.

#### Scenario: Operator enables SSH debug
- **WHEN** a manually dispatched production or PR validation workflow starts the public-key-authenticated debug session
- **THEN** production secrets SHALL be absent from the debug process environment
- **AND** decoded private keys, certificates, profiles, and temporary keychains SHALL already be destroyed or inaccessible to the debug processes
- **AND** the checked-out repository SHALL NOT retain an ambient repository token readable during the session
- **AND** the session SHALL retain its authentication, timeout, and audit controls

#### Scenario: Production step consumes a credential
- **WHEN** a production step requires an Apple, repository, publication, revalidation, or notification credential
- **THEN** only that step SHALL receive the required credential
- **AND** unrelated setup, reporting, cache, and debug steps SHALL NOT inherit it from job scope
