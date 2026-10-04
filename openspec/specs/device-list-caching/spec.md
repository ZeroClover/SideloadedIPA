# device-list-caching Specification

## Purpose
Define device-state snapshots and device-driven invalidation of provisioning and signed artifacts.

## Requirements

### Requirement: Device List Snapshot
The system SHALL collect enabled iOS device evidence within the current Apple signing-resource transaction and bind normalized device state into its resource snapshot.

#### Scenario: Fetch current device list
- **WHEN** a resource plan or synchronization collects current signing prerequisites
- **THEN** it SHALL enumerate enabled iOS devices through the documented API
- **AND** normalized state SHALL include resource identity, name, platform, class, status and a SHA-256 digest of the UDID
- **AND** retained reports SHALL NOT disclose raw UDIDs

#### Scenario: Generate deterministic device evidence
- **WHEN** the same device collection is returned in a different order
- **THEN** normalized snapshot identity SHALL remain deterministic
- **AND** changed normalized device state SHALL change the bound resource snapshot digest

#### Scenario: Reuse devices within the transaction
- **WHEN** reconciliation processes multiple target bundles
- **THEN** it SHALL use the held device collection rather than re-enumerating it per bundle
- **AND** profile requests SHALL use the exact eligible device resource set

### Requirement: Device List Comparison
The system SHALL incorporate current resource and validated profile-device evidence into the complete signing fingerprint, rather than relying on an independent device-list cache or global device-rebuild flag.

#### Scenario: Bind profile device eligibility
- **WHEN** a profile is validated and persisted for a target bundle
- **THEN** its eligibility SHALL agree with the current device request
- **AND** the profile manifest SHALL include a digest of normalized device identities
- **AND** that digest and current resource snapshot SHALL participate in signing identity

#### Scenario: Device evidence changes
- **WHEN** current device or validated profile evidence changes a selected task's complete fingerprint
- **THEN** the prior fingerprint SHALL NOT qualify as an unchanged cache hit
- **AND** the selected task SHALL require signing from current validated inputs

#### Scenario: No complete cache record exists
- **WHEN** a selected task has no complete signing-cache record
- **THEN** it SHALL require initial signing and independent verification
- **AND** a legacy standalone device-cache file SHALL NOT be a readiness prerequisite

### Requirement: Full Rebuild Trigger
The system SHALL reconcile profile eligibility before selecting signing reuse and rebuild affected selected tasks when their complete signing inputs change; an explicit operator force request SHALL rebuild every selected task.

#### Scenario: Profile has stale device relationships
- **WHEN** an existing profile's relationships differ from the exact eligible device set
- **THEN** authorized synchronization SHALL obtain an additive validated replacement
- **AND** the old profile SHALL NOT be automatically deleted
- **AND** changed profile evidence SHALL invalidate prior signing identity

#### Scenario: Devices unchanged but another prerequisite changed
- **WHEN** devices are unchanged but a profile is expired, near expiry, unauthorized, or mismatched to certificate or bundle
- **THEN** synchronization SHALL NOT skip validation or required replacement merely because devices are unchanged
- **AND** cache reuse SHALL still require all current prerequisites and independent artifact verification

#### Scenario: No eligible devices
- **WHEN** a development-profile request requires devices but its eligible device set is empty
- **THEN** reconciliation SHALL block rather than create an unusable profile or reuse unchecked artifact evidence
