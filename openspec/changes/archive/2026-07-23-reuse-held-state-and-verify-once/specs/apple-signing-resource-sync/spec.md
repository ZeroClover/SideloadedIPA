## ADDED Requirements

### Requirement: Bounded Apple state collection

The system SHALL assemble one normalized Apple state snapshot per synchronization transaction using at most one enumeration per resource collection, SHALL obtain relationship and content data from enumeration responses, documented mutation responses, or targeted single-resource reads, and SHALL NOT re-enumerate a collection that the transaction cannot mutate.

#### Scenario: Collect one transaction snapshot

- **WHEN** a plan or apply transaction collects Apple signing state
- **THEN** bundle identifiers, certificates, devices, and profiles SHALL each be enumerated at most once for the transaction
- **AND** capability enumeration SHALL be scoped to the transaction's managed App IDs

#### Scenario: Collections the transaction cannot mutate are read once

- **WHEN** reconciliation performs additive mutations, which affect only App IDs, capabilities, and profiles
- **THEN** certificate and device collections SHALL NOT be enumerated again within the same transaction
- **AND** intermediate state refreshes SHALL merge documented mutation results into the held snapshot instead of re-enumerating unchanged collections

#### Scenario: Reuse profile content captured at enumeration

- **WHEN** profile enumeration yields a profile's content and its digest
- **THEN** profile validation within the same transaction SHALL consume those held bytes without a second download
- **AND** the validated bytes SHALL be bound to the enumerated state by digest identity
- **AND** a digest mismatch SHALL fail closed or trigger a validated additive replacement according to the existing reconciliation policy

#### Scenario: Verify a created profile with a targeted read

- **WHEN** a profile creation succeeds with a definite API response
- **THEN** synchronization SHALL verify the created resource and its bundle, certificate, and device relationships through a single-resource read of that profile
- **AND** SHALL NOT re-enumerate the entire profile collection to locate the created resource
- **AND** the verified created state SHALL be merged into the held snapshot for later targets and final evidence

## MODIFIED Requirements

### Requirement: Additive explicit App ID reconciliation

The system SHALL reconcile one exact explicit App ID for every profile-bearing target bundle using the transaction snapshot for existence decisions and MAY automatically create a missing App ID only through a documented official API.

#### Scenario: App ID already exists

- **WHEN** an exact explicit target identifier is present in the transaction snapshot and belongs to the authenticated team
- **THEN** the operation SHALL be `no-op` without an additional enumeration call
- **AND** its stable resource ID SHALL be recorded in the manifest

#### Scenario: App ID is missing and creation is authorized

- **WHEN** an exact target identifier is absent from the transaction snapshot, creation is enabled, and the API role permits creation
- **THEN** apply mode SHALL create it once using an idempotent lookup-after-retry strategy
- **AND** SHALL verify the returned identifier from the documented create response before continuing
- **AND** SHALL merge the created resource into the transaction snapshot without a fresh enumeration

#### Scenario: Uncertain create result is recovered

- **WHEN** an App ID creation attempt ends with a timeout, availability failure, or conflict that leaves the result uncertain
- **THEN** the reconciler SHALL re-enumerate the bundle identifier collection and match the exact intended resource before retrying creation
- **AND** recovery re-enumeration SHALL be limited to the affected collection

#### Scenario: Destructive identifier change is implied

- **WHEN** configuration no longer references an existing App ID or changes a target identifier
- **THEN** the system SHALL NOT delete or mutate the old App ID
- **AND** optional cleanup SHALL be reported as a separate human action

### Requirement: Capability automation boundary

The system SHALL automatically enable only requested additive capability changes exposed by the documented API adapter, SHALL decide capability existence from the transaction snapshot, and SHALL never silently downgrade or disable a capability.

#### Scenario: Capability is already satisfied in the snapshot

- **WHEN** the transaction snapshot already contains the exact requested capability for the target App ID
- **THEN** the operation SHALL be `no-op` without an additional enumeration call

#### Scenario: Supported capability is absent

- **WHEN** a requested capability and settings are supported by the official API and absent from the App ID
- **THEN** apply mode SHALL enable the exact additive capability
- **AND** SHALL verify its state from the documented mutation response, using a targeted re-read only when that response does not include the created state
- **AND** SHALL merge the verified capability into the transaction snapshot

#### Scenario: Capability is unsupported or approval-gated

- **WHEN** a requested capability lacks a documented API operation, requires Account Holder action, requires Apple approval, or is unavailable to the team/profile type
- **THEN** the resource plan SHALL classify it as `manual-required` or `blocked`
- **AND** SHALL name the affected bundle, capability, required role, and verification evidence

#### Scenario: Capability removal is requested

- **WHEN** current Apple state contains a capability that policy no longer requests
- **THEN** CI SHALL leave the capability unchanged
- **AND** SHALL NOT invalidate profiles through automatic removal
