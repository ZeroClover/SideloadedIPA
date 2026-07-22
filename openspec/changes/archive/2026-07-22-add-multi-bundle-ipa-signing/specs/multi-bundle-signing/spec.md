## ADDED Requirements

### Requirement: Complete immutable signing plan

The system MUST construct and validate an immutable signing plan before changing bundle identifiers, entitlements, signatures, or package contents.

#### Scenario: Build a complete plan

- **WHEN** inventory, configuration, Apple resource, profile, and certificate inputs are valid
- **THEN** the plan SHALL identify every signable node, target identifier when applicable, profile mapping, expected-entitlement hash, certificate fingerprint, signing order, backend, and source/output digest
- **AND** SHALL have a canonical plan digest

#### Scenario: Profile mapping is incomplete

- **WHEN** a profile-bearing node has zero or multiple candidate profiles, or a supplied profile is unused
- **THEN** planning SHALL fail before archive mutation
- **AND** SHALL list the conflicting bundle and profile identities

#### Scenario: Team or certificate differs across profiles

- **WHEN** planned profiles do not resolve to the same intended team and certificate identity
- **THEN** signing SHALL be blocked

### Requirement: Qualified per-bundle signing backend

The system SHALL use a version-verified backend that can apply the plan's distinct profile and entitlement policy to every profile-bearing bundle.

#### Scenario: Use a qualified zsign backend

- **WHEN** the pinned zsign version has passed the multi-bundle backend contract fixture
- **THEN** the adapter SHALL pass all planned profiles using repeated `-m` arguments
- **AND** SHALL pass each profile-bearing bundle's planned entitlement document using the paired repeated `-e` extension selected by ADR 0001
- **AND** SHALL reject profile/entitlement count mismatches before signing
- **AND** SHALL prove the profile selected for each bundle from post-sign evidence

#### Scenario: Backend only supports one global entitlement document

- **WHEN** planned bundles require different entitlement documents and the backend cannot apply them independently
- **THEN** backend qualification SHALL fail before production signing
- **AND** the system SHALL require a per-bundle-capable backend or backend extension
- **AND** SHALL NOT substitute profile-only or global entitlements without passing the expected contract

#### Scenario: Backend version is unexpected

- **WHEN** the executable version or checksum does not match the configured supported release
- **THEN** signing SHALL fail before the certificate or source IPA is used

### Requirement: Deepest-first recursive signing

The system SHALL sign nested code from deepest to shallowest and SHALL sign the root application last.

#### Scenario: Extension contains frameworks

- **WHEN** an extension contains frameworks or dylibs
- **THEN** those nested code objects SHALL be signed before the extension executable
- **AND** the extension SHALL be signed before the root app

#### Scenario: Root contains multiple extensions

- **WHEN** several extensions are siblings in the graph
- **THEN** each complete extension subtree SHALL be signed before the root
- **AND** sibling ordering SHALL be deterministic

### Requirement: Correct handling of profile-free nested code

The system SHALL re-sign frameworks, dylibs, and other supported profile-free executable nodes with the planned signing identity without assigning them App IDs or provisioning profiles.

#### Scenario: Sign a framework and dylib

- **WHEN** inventory contains a framework executable and a standalone dylib
- **THEN** both SHALL receive fresh valid signatures before their containing bundle
- **AND** neither SHALL receive an embedded provisioning profile or application entitlements

#### Scenario: Nested code is omitted by the backend

- **WHEN** post-sign evidence shows a discovered executable node retained an old, ad-hoc, or invalid signature
- **THEN** the signing result SHALL be considered failed

### Requirement: Planned identifier transformation

The system SHALL apply target bundle identifiers exactly as expressed by the plan and SHALL NOT rely on an unchecked blanket root rewrite.

#### Scenario: Rewrite root and derived extension identifiers

- **WHEN** a source root and descendant extension are mapped by suffix preservation
- **THEN** their `CFBundleIdentifier` values and application-identifier entitlements SHALL match their respective planned targets

#### Scenario: Use an explicit nested override

- **WHEN** one nested bundle has an explicit target override
- **THEN** signing SHALL preserve that override even if it differs from a simple root-prefix replacement

### Requirement: Safe subprocess invocation

The system SHALL invoke signing tools with explicit argv values, no command shell, bounded execution, captured redacted output, and typed failure handling.

#### Scenario: Path contains spaces or shell metacharacters

- **WHEN** a certificate, profile, entitlement, input, or output path contains spaces or shell metacharacters
- **THEN** it SHALL be passed as one argv element
- **AND** no part of the value SHALL be evaluated by a shell

#### Scenario: Signing process times out or exits nonzero

- **WHEN** the backend exceeds its timeout or reports failure
- **THEN** the stage SHALL terminate with bundle/backend context
- **AND** SHALL NOT promote or publish the partial output

### Requirement: Isolated and atomic output production

The system SHALL sign a workspace copy and SHALL expose a result artifact only after backend completion and verification.

#### Scenario: Signing succeeds

- **WHEN** all nodes are signed and verification passes
- **THEN** the verified IPA SHALL be atomically promoted to the task result path
- **AND** the original downloaded IPA SHALL remain unchanged

#### Scenario: Signing fails midway

- **WHEN** any nested or root signing operation fails
- **THEN** the incomplete workspace and temporary output SHALL be discarded
- **AND** a previous verified output SHALL not be overwritten

### Requirement: Backend provenance

The system SHALL record backend name, version, executable checksum, argv shape with secrets redacted, plan digest, timings, and per-node result evidence.

#### Scenario: Signing report is generated

- **WHEN** a signing attempt completes or fails
- **THEN** the report SHALL make the selected backend and affected bundle node traceable
- **AND** SHALL NOT expose P12 passwords, private keys, or raw profile content

### Requirement: Unified engine for single- and multi-bundle tasks

The final migrated system SHALL execute legacy single-bundle and configured multi-bundle tasks through the same plan, backend, and verification interfaces.

#### Scenario: Sign a legacy root-only task after migration

- **WHEN** a compatible single-bundle task is run through the new engine
- **THEN** it SHALL create a one-node profile plan plus its nested profile-free code
- **AND** SHALL preserve existing output naming and publication identity
