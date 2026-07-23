# apple-signing-resource-sync Specification

## Purpose
Define additive, auditable reconciliation of Apple identifiers, capabilities, certificates, devices, and provisioning profiles required by the current IPA bundle graph.
## Requirements
### Requirement: Read-only Apple resource plan before apply

The system SHALL inspect current Apple signing resources and produce a complete resource plan before performing any mutation.

#### Scenario: Plan resources for a new multi-bundle task

- **WHEN** valid inventory and bundle policies require App IDs, capabilities, App Groups, certificates, devices, and profiles
- **THEN** the planner SHALL query current official API state
- **AND** SHALL classify every required operation as `no-op`, `safe-automatic`, `manual-required`, or `blocked`
- **AND** SHALL emit the plan without mutation in plan mode

#### Scenario: Planning cannot read required state

- **WHEN** credentials, role, API availability, or response parsing prevents a reliable resource decision
- **THEN** planning SHALL fail with a redacted diagnostic
- **AND** apply mode SHALL NOT guess or continue from partial state

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

### Requirement: Explicit App Group prerequisite

The system SHALL treat App Group container registration and any relationship not exposed by a verified documented API as human prerequisites.

#### Scenario: Required App Group is present and associated

- **WHEN** the configured App Group exists for the team and every relevant App ID is associated with it
- **THEN** the resource plan SHALL record either documented API evidence or an explicit reviewed operator confirmation when the relationship is not exposed by the public API
- **AND** profile creation MAY proceed
- **AND** each created or reused profile SHALL still authorize the exact configured App Group

#### Scenario: App Group is missing

- **WHEN** a configured App Group container does not exist
- **THEN** the plan SHALL block profile creation for affected bundles
- **AND** SHALL provide a manual Developer Portal/Xcode action for an Account Holder or Admin
- **AND** SHALL NOT call a private or browser-scraped endpoint

### Requirement: Exact signing certificate resolution

The system SHALL bind profiles and signing to the same currently valid certificate represented by the configured P12.

#### Scenario: Match P12 to one Apple certificate

- **WHEN** the P12 public key or certificate fingerprint matches exactly one valid development certificate in Apple state
- **THEN** the resource manifest SHALL record that certificate resource ID and a non-secret SHA-256 fingerprint
- **AND** every generated profile SHALL include that certificate

#### Scenario: Certificate match is absent or ambiguous

- **WHEN** the P12 matches zero or more than one usable Apple certificate resource
- **THEN** resource planning SHALL fail before profile creation
- **AND** SHALL NOT select all certificates or choose by display name alone

### Requirement: One validated profile per profile-bearing bundle

The system SHALL create, refresh, download, and store profiles by task and target bundle identity rather than one filename per task.

#### Scenario: Existing profile is fully current

- **WHEN** a profile has the exact App ID, resolved certificate, required device set, profile type, sufficient validity window, and authorized entitlements
- **THEN** it MAY be reused
- **AND** its stable resource ID, expiry, and SHA-256 fingerprint SHALL be recorded

#### Scenario: Profile is stale

- **WHEN** the certificate, device set, capability state, App Group relationship, profile type, expiration threshold, or entitlement authorization differs
- **THEN** apply mode SHALL generate and download a replacement profile
- **AND** SHALL validate the replacement before exposing it to signing
- **AND** SHALL NOT auto-delete the old profile

#### Scenario: Multi-bundle application is synchronized

- **WHEN** an inventory has multiple profile-bearing bundles with valid target policies
- **THEN** the manifest SHALL contain one distinct validated profile per inventory node mapped one-to-one to its target identifier
- **AND** the profile count SHALL equal the planned profile-bearing bundle count

### Requirement: Profile authorization validation

The system MUST decode every downloaded profile and validate its team, application identifier, profile type, certificate, device eligibility, dates, and entitlement authorization before signing.

#### Scenario: Profile authorizes the bundle policy

- **WHEN** all identity, certificate, device, date, and entitlement checks pass
- **THEN** the profile SHALL be marked usable for exactly that bundle

#### Scenario: Profile lacks a required entitlement

- **WHEN** the expected entitlement document requests a value the profile does not authorize
- **THEN** resource synchronization SHALL fail for that bundle with the missing key/value
- **AND** the profile SHALL NOT be passed to the signing backend

#### Scenario: Profile belongs to another bundle or team

- **WHEN** a downloaded profile's team or application identifier does not exactly match its planned target
- **THEN** validation SHALL fail even if its filename or display name appears correct

### Requirement: Idempotent, auditable, and secret-safe reconciliation

The system SHALL make safe automatic operations retryable and SHALL redact credentials, private keys, raw P12 data, and raw profiles from logs and reports.

#### Scenario: Retry after an uncertain API response

- **WHEN** creation times out after the server may have accepted it
- **THEN** the reconciler SHALL re-list and match the exact intended resource before retrying creation
- **AND** SHALL avoid duplicate resources where the API permits deterministic matching

#### Scenario: Run after partial successful apply

- **WHEN** an earlier run created some App IDs or profiles and then failed
- **THEN** the next plan SHALL classify existing valid resources as `no-op`
- **AND** SHALL continue only the remaining safe actions

#### Scenario: Emit diagnostics

- **WHEN** resource planning or apply completes
- **THEN** reports SHALL contain stable resource IDs and hashes needed for audit
- **AND** SHALL exclude credential values, private profile payloads, and certificate private material

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
