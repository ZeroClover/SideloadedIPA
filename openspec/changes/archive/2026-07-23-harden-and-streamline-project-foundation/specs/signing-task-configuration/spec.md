## ADDED Requirements

### Requirement: Fixed safe signing policy invariants
The system MUST apply preserve-source-suffix mapping, fail-closed unknown-profile-bundle handling, and iOS development profile selection as internal invariants rather than user-selectable configuration.

#### Scenario: Load a multi-bundle signing table
- **WHEN** configuration contains valid bundle rules and App Group mappings
- **THEN** the typed signing policy SHALL receive the fixed safe identifier, unknown-bundle, and profile behavior
- **AND** the task SHALL NOT need fields that repeat those invariants

#### Scenario: Configuration contains a removed single-choice field
- **WHEN** a signing table declares `id_strategy`, `unknown_profile_bundles`, or `profile_type`
- **THEN** validation SHALL fail with a migration diagnostic naming the redundant field
- **AND** it SHALL instruct the operator to remove the field rather than select another value

#### Scenario: Repository configuration migrates
- **WHEN** this change is applied
- **THEN** production, example, fixture, documentation, and schema tests SHALL remove all three fields together
- **AND** the resulting target identifiers, unknown-bundle failures, and profile requests SHALL remain unchanged

## MODIFIED Requirements

### Requirement: Declarative per-bundle signing policy
The system SHALL support a task-scoped signing policy that declares named App Groups and rules matched by source bundle identifier while package invariants supply identifier derivation, unknown-bundle handling, and profile type.

#### Scenario: Configure a multi-bundle task
- **WHEN** a task declares a valid signing table and bundle rules
- **THEN** each rule SHALL decode into typed source identifier, optional target identifier, required capabilities, entitlement mode, and entitlement source
- **AND** the resulting policy SHALL be independent of filesystem basenames and inventory ordering

#### Scenario: Duplicate source rule
- **WHEN** two rules can match the same profile-bearing source bundle
- **THEN** configuration validation SHALL fail with both rule locations
- **AND** no rule precedence SHALL be inferred

#### Scenario: Plan Apple profile resources
- **WHEN** a valid target bundle policy reaches Apple resource planning
- **THEN** the system SHALL request the package-owned iOS development profile type
- **AND** task configuration SHALL NOT override it

### Requirement: Deterministic target identifier mapping
The system SHALL map every profile-bearing source identifier to exactly one unique explicit target identifier using preserve-source-suffix derivation unless a rule declares an override.

#### Scenario: Preserve a source suffix
- **WHEN** a nested source identifier is a descendant of the source root identifier
- **THEN** the target identifier SHALL be the configured target root followed by the unchanged source suffix

#### Scenario: Override a derived target
- **WHEN** a bundle rule declares an explicit target identifier
- **THEN** that exact identifier SHALL be used instead of the derived identifier
- **AND** it SHALL still pass identifier syntax and uniqueness validation

#### Scenario: Encounter a non-descendant identifier
- **WHEN** a nested source identifier is not a descendant of the source root and no explicit target is configured
- **THEN** validation SHALL fail with a requirement for an explicit mapping

#### Scenario: Target identifiers collide
- **WHEN** two inventory nodes map to the same target identifier
- **THEN** signing-policy validation SHALL fail before Apple resource planning

### Requirement: Complete inventory-to-policy match
The system MUST reconcile the signing policy against the actual bundle inventory and MUST reject every uncovered profile-bearing bundle before any Apple resource mutation.

#### Scenario: Every profile-bearing bundle is planned
- **WHEN** every profile-bearing inventory node matches exactly one allowed rule or declared deterministic default
- **THEN** the system SHALL produce one target policy per node

#### Scenario: Upstream adds an extension
- **WHEN** an inventory contains a profile-bearing bundle that is not covered by policy
- **THEN** planning SHALL fail with its path and source identifier
- **AND** the new extension SHALL NOT inherit the root profile

#### Scenario: Configuration references an absent bundle
- **WHEN** a required bundle rule matches no inventory node
- **THEN** planning SHALL fail with the missing source identifier
- **AND** SHALL distinguish a wrong release asset from a changed upstream graph
