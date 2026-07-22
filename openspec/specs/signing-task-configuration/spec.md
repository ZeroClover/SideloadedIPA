# signing-task-configuration Specification

## Purpose
TBD - created by archiving change add-multi-bundle-ipa-signing. Update Purpose after archive.
## Requirements
### Requirement: Backwards-compatible single-bundle tasks

The system SHALL continue to accept existing task entries that define a root `bundle_id` and no multi-bundle signing table when the IPA contains only one profile-bearing application bundle.

#### Scenario: Load an existing single-bundle task

- **WHEN** a valid existing task has no `tasks.signing` table and its inventory has only the root profile-bearing bundle
- **THEN** configuration SHALL produce a root-only signing policy using the existing `bundle_id`
- **AND** existing source, slug, icon, cache, and publication behavior SHALL remain compatible

#### Scenario: Legacy task contains unconfigured extensions

- **WHEN** a task without multi-bundle policy inventories one or more nested profile-bearing bundles
- **THEN** validation SHALL fail with the discovered source identifiers
- **AND** SHALL instruct the operator to add explicit multi-bundle policy rather than signing only the root

### Requirement: Declarative per-bundle signing policy

The system SHALL support a task-scoped signing policy that declares identifier strategy, unknown-bundle behavior, profile type, named App Groups, and rules matched by source bundle identifier.

#### Scenario: Configure a multi-bundle task

- **WHEN** a task declares a valid signing table and bundle rules
- **THEN** each rule SHALL decode into typed source identifier, optional target identifier, required capabilities, entitlement mode, and entitlement source
- **AND** the resulting policy SHALL be independent of filesystem basenames and inventory ordering

#### Scenario: Duplicate source rule

- **WHEN** two rules can match the same profile-bearing source bundle
- **THEN** configuration validation SHALL fail with both rule locations
- **AND** no rule precedence SHALL be inferred

### Requirement: Deterministic target identifier mapping

The system SHALL map every profile-bearing source identifier to exactly one unique explicit target identifier.

#### Scenario: Preserve a source suffix

- **WHEN** `preserve-source-suffix` is selected and a nested source identifier is a descendant of the source root identifier
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

The system MUST reconcile the signing policy against the actual bundle inventory before any Apple resource mutation.

#### Scenario: Every profile-bearing bundle is planned

- **WHEN** every profile-bearing inventory node matches exactly one allowed rule or declared deterministic default
- **THEN** the system SHALL produce one target policy per node

#### Scenario: Upstream adds an extension

- **WHEN** an inventory contains a profile-bearing bundle that is not covered and `unknown_profile_bundles` is `error`
- **THEN** planning SHALL fail with its path and source identifier
- **AND** the new extension SHALL NOT inherit the root profile

#### Scenario: Configuration references an absent bundle

- **WHEN** a required bundle rule matches no inventory node
- **THEN** planning SHALL fail with the missing source identifier
- **AND** SHALL distinguish a wrong release asset from a changed upstream graph

### Requirement: Explicit entitlement policy

The system SHALL require every profile-bearing bundle in a multi-bundle task to use a supported entitlement mode and SHALL materialize a deterministic expected entitlement document.

#### Scenario: Use profile mode

- **WHEN** a bundle selects `profile` mode
- **THEN** the expected functional requirements SHALL still be checked against the profile-derived document
- **AND** missing required values SHALL fail before signing

#### Scenario: Preserve source entitlements

- **WHEN** a bundle selects `preserve-source` mode
- **THEN** source values SHALL be preserved except for declared typed identifier, team-prefix, and App Group transformations
- **AND** every transformation SHALL appear in the plan report

#### Scenario: Use an entitlement template

- **WHEN** a bundle selects `template` mode
- **THEN** the system SHALL load a repository-controlled plist and expand only supported typed placeholders
- **AND** SHALL reject missing files, paths outside the configured repository area, unknown placeholders, and type-changing expansion

#### Scenario: Intentionally remove an upstream entitlement

- **WHEN** policy would remove a source entitlement that can affect functionality or security
- **THEN** validation SHALL require an explicit allowed-drop entry with a non-empty rationale
- **AND** undeclared removal SHALL be blocking

### Requirement: Named App Group mapping

The system SHALL let bundle policies reference task-scoped App Group aliases whose values are explicit identifiers owned or managed by the signing team.

#### Scenario: Share one group across cooperating bundles

- **WHEN** root and extension policies reference the same App Group alias
- **THEN** every expected entitlement document SHALL expand to the same configured group identifier
- **AND** the Apple resource plan SHALL check that each relevant App ID is associated with it

#### Scenario: Upstream group is not owned by the team

- **WHEN** source entitlements name an upstream App Group and policy maps it to a team-owned alias
- **THEN** the expected document SHALL contain only the declared target mapping
- **AND** the system SHALL NOT attempt to register or claim the upstream identifier

### Requirement: Aggregated side-effect-free validation

The system SHALL report all configuration and inventory-policy validation errors that can be determined in one pass before applying Apple changes or signing.

#### Scenario: Configuration has multiple independent errors

- **WHEN** target-ID collision, unmatched bundles, and invalid entitlement placeholders are all present
- **THEN** validation SHALL report each error with task, bundle, field, and remediation context
- **AND** SHALL exit without mutating Apple, cache-success, signed-artifact, or publication state
