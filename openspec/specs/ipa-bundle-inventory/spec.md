# ipa-bundle-inventory Specification

## Purpose
TBD - created by archiving change add-multi-bundle-ipa-signing. Update Purpose after archive.
## Requirements
### Requirement: Safe immutable IPA intake

The system MUST inspect an IPA in an isolated task-scoped workspace without modifying the downloaded source and MUST reject archive entries that could escape or ambiguously populate that workspace.

#### Scenario: Inspect a valid IPA

- **WHEN** a downloaded asset is a valid IPA within configured resource limits
- **THEN** the system SHALL calculate and record its SHA-256 digest
- **AND** extraction SHALL occur in a fresh task-scoped directory
- **AND** the source asset SHALL remain byte-for-byte unchanged

#### Scenario: Reject an unsafe archive path

- **WHEN** an archive contains an absolute path, parent traversal, NUL name, duplicate normalized path, symbolic link, or special-file entry
- **THEN** inventory SHALL fail before any entry is written outside the isolated workspace
- **AND** no Apple resource, signed output, cache success state, or publication state SHALL be changed

#### Scenario: Reject an archive bomb

- **WHEN** the entry count, per-entry expansion, total uncompressed size, or compression ratio exceeds configured limits
- **THEN** inventory SHALL stop with a resource-limit diagnostic
- **AND** partially extracted data SHALL be discarded

### Requirement: Unambiguous root application

The system SHALL require exactly one root application bundle at `Payload/*.app` and SHALL treat that bundle as the root of the signing graph.

#### Scenario: Discover one root app

- **WHEN** an IPA contains exactly one valid `Payload/<name>.app` with a readable `Info.plist` and executable
- **THEN** the system SHALL identify it as the root bundle
- **AND** SHALL record its bundle identifier, version, executable path, and content hashes

#### Scenario: Reject an ambiguous payload

- **WHEN** an IPA contains zero or more than one root application bundle
- **THEN** inventory SHALL fail with the candidate paths
- **AND** no root SHALL be selected heuristically

### Requirement: Complete bundle and signable-code graph

The system SHALL recursively discover all supported profile-bearing bundles and nested signable code and SHALL record explicit parent-child edges and signing depth.

#### Scenario: Discover extensions and nested code

- **WHEN** a root app contains app extensions, nested apps, frameworks, dylibs, or executable Mach-O files
- **THEN** the inventory SHALL include every supported node with its relative path, kind, parent, depth, source bundle identifier when applicable, executable hash, and profile requirement
- **AND** profile-bearing bundles SHALL be distinguishable from code that only requires recursive signing

#### Scenario: Discover frameworks inside an extension

- **WHEN** an app extension contains its own frameworks or dylibs
- **THEN** those nodes SHALL be children of that extension
- **AND** their signing depth SHALL place them before the extension and root app

#### Scenario: Encounter unknown executable content

- **WHEN** the inspector finds executable code or a bundle type whose signing semantics are unsupported
- **THEN** inventory SHALL fail closed with the path and detected type
- **AND** the code SHALL NOT be silently copied with its old signature

### Requirement: Original metadata and entitlement evidence

The system SHALL capture the original `Info.plist`, embedded-profile presence, and executable entitlement evidence for every profile-bearing bundle before identifier rewriting or signing.

#### Scenario: Read XML and DER entitlements

- **WHEN** a bundle executable contains XML entitlements, DER entitlements, or both
- **THEN** the inspector SHALL decode every available representation into a canonical typed form
- **AND** SHALL retain hashes of the raw evidence
- **AND** SHALL report disagreement between XML and DER forms as a blocking diagnostic

#### Scenario: Source has no provisioning profile

- **WHEN** a profile-bearing bundle has no embedded provisioning profile
- **THEN** the inventory SHALL record that absence without treating it as an extraction error
- **AND** later planning SHALL still require a target profile for that bundle

#### Scenario: Entitlements cannot be decoded

- **WHEN** a profile-bearing executable contains an unreadable or unsupported entitlement payload
- **THEN** inventory SHALL fail for that bundle
- **AND** the system SHALL NOT assume an empty entitlement contract

### Requirement: Deterministic inventory manifest

The system SHALL serialize the graph and its evidence as canonical, schema-versioned JSON with a deterministic digest.

#### Scenario: Repeat inventory of identical input

- **WHEN** the same IPA bytes are inventoried twice with the same inspector schema
- **THEN** node ordering, canonical manifest content, and graph digest SHALL be identical

#### Scenario: Bundle graph changes upstream

- **WHEN** a later asset adds, removes, renames, or changes a profile-bearing or signable node
- **THEN** the graph digest SHALL change
- **AND** downstream cache and configuration matching SHALL re-evaluate that task
