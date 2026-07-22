## MODIFIED Requirements

### Requirement: Ordered staged workflow

The system SHALL coordinate source resolution, safe inventory, configuration matching, Apple resource planning/apply, signing-plan validation, signing, output verification, and publication as explicit ordered stages in production and manually dispatched canaries.

#### Scenario: Run a task successfully

- **WHEN** every stage completes successfully and no manual prerequisite remains
- **THEN** each downstream stage SHALL consume the typed manifest from its predecessor
- **AND** publication SHALL occur only after verification

#### Scenario: An early stage fails

- **WHEN** any production stage fails
- **THEN** all later mutation and publication stages SHALL be skipped
- **AND** production failure-injection tests SHALL prove that later adapters and side effects are not invoked
- **AND** the final report SHALL identify the first blocking stage and all available validation findings

#### Scenario: Operator runs a private multi-bundle canary

- **WHEN** the operator manually enables the non-publishing canary
- **THEN** the canary SHALL execute the production inspect, plan, apply, sign, and standalone verify chain under one run identity
- **AND** SHALL validate the current production result and run-report schemas
- **AND** SHALL NOT receive publication credentials or invoke publication

### Requirement: Structured diagnostics and provenance

The system SHALL emit concise human output and a schema-versioned redacted JSON run report across local and CI execution.

#### Scenario: Run report is complete

- **WHEN** a run finishes
- **THEN** the report SHALL include measured stage status/timing, source release and digest, graph and plan digests, bundle mappings, capability classifications, manual actions, Apple resource IDs, non-secret certificate/profile/tool fingerprints, verification results, cache decisions, and publication outcome
- **AND** timing that cannot be observed at the represented granularity SHALL be null or omitted rather than reported as a fabricated zero

#### Scenario: Secret appears in adapter output

- **WHEN** a subprocess or API error includes a known credential or secret path/value
- **THEN** log/report handling SHALL redact it before display or artifact retention

### Requirement: CLI and workflow migration compatibility

The system SHALL retain an operational compatibility wrapper only while it has a supported caller or parity acceptance remains incomplete.

#### Scenario: Supported caller uses a legacy entry point

- **WHEN** an operational caller still uses a legacy script path during migration
- **THEN** the wrapper SHALL delegate to package use cases without duplicating business rules
- **AND** SHALL preserve documented environment inputs and exit behavior or provide an explicit migration diagnostic

#### Scenario: Production parity is accepted

- **WHEN** all configured tasks pass production parity and repository searches show no supported caller for a legacy selector
- **THEN** that selector, its compatibility alias, and obsolete characterization contract SHALL be removed
- **AND** production SHALL continue to use only package-owned cache decisions

### Requirement: Compensating cleanup for failed publication

The publication transaction SHALL remove newly uploaded immutable objects that are not referenced after the transaction fails.

#### Scenario: Batch upload or registry promotion fails

- **WHEN** one or more new IPA or icon objects were uploaded but the batch registry was not successfully promoted and revalidated
- **THEN** the previous registry SHALL remain or be restored
- **AND** the gateway SHALL attempt deletion of only the unreferenced keys uploaded by that attempt
- **AND** any cleanup failure SHALL report every remaining IPA and icon key without masking the original publication failure

### Requirement: Debug sessions use least-privilege credentials

An SSH debug step MUST NOT inherit production signing, Apple API, object-storage, revalidation, or repository credentials unless a separately reviewed debug operation explicitly requires an individual credential.

#### Scenario: Operator enables SSH debug

- **WHEN** the workflow starts the public-key-authenticated debug session
- **THEN** production secrets SHALL be absent from the debug process environment
- **AND** decoded private keys, certificates, profiles, and temporary keychains SHALL already be destroyed
- **AND** the session SHALL retain its authentication, timeout, and audit controls

#### Scenario: Non-production job consumes a credential

- **WHEN** a shadow, probe, or qualification step requires an Apple or repository credential
- **THEN** only that step SHALL receive the required credential
- **AND** unrelated setup, reporting, artifact upload, cleanup, and debug steps SHALL NOT inherit it from job scope
