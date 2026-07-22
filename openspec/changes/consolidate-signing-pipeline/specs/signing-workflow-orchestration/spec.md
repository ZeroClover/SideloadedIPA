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

#### Scenario: Mode-scoped dispatch input lacks its owning mode

- **WHEN** a manual dispatch sets a mode-scoped input, such as a qualification apply or reset option, without enabling the mode that owns it
- **THEN** the workflow SHALL fail before any job that receives credentials or performs mutations starts
- **AND** SHALL NOT route the dispatch into the production publish path
- **AND** static workflow tests SHALL assert the production job condition excludes such dispatches

### Requirement: Complete production evidence

Production execution SHALL retain canonical stage manifests, one schema-versioned redacted run report, and a cancellation report when interrupted.

#### Scenario: Production run completes

- **WHEN** the selected batch succeeds or fails
- **THEN** its retained report SHALL contain the actual stage timings, source and plan provenance, cache decisions, verification findings, publication outcome, and diagnostics
- **AND** fixture-generated or shadow-only evidence SHALL NOT substitute for the production report

#### Scenario: Production run is cancelled

- **WHEN** execution is interrupted after local or remote side effects begin, including interruption by the POSIX termination signal that CI cancellation delivers
- **THEN** temporary work SHALL be cleaned where safe
- **AND** created Apple resource identities, publication commit state, and unresolved cleanup actions SHALL be recorded without secrets

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

#### Scenario: Migration debt reaches its end state

- **WHEN** repository searches show no supported caller for a remaining legacy delegator, superseded command layer, fixture-only orchestration engine, or unused protocol seam
- **THEN** those modules, their delegator scripts, and the tests that exist only to keep them covered SHALL be removed together
- **AND** production SHALL execute through exactly one package-owned orchestration engine

#### Scenario: Production code depends on an exempt module

- **WHEN** production orchestration, signing, or publication imports a module excluded from strict typing or the coverage gate
- **THEN** that module SHALL be promoted into a gated package location
- **AND** the typing and coverage exemptions SHALL be removed with the promotion

### Requirement: Production acceptance for new multi-bundle tasks

The system SHALL keep a newly enabled multi-bundle task non-publishing until automated verification and the task's reviewed registered-device acceptance contract both pass for the selected source asset.

#### Scenario: Automated canary passes but device acceptance is absent

- **WHEN** a new multi-bundle task passes inventory, resource, signing, and verification gates but has no current physical-device acceptance record
- **THEN** redacted qualification evidence and run reports MAY be retained for review
- **AND** signed artifacts embedding registered-device provisioning material SHALL NOT be uploaded to shared CI artifact storage
- **AND** production registry publication SHALL remain disabled

#### Scenario: Complete physical-device acceptance

- **WHEN** an operator records successful completion of every behavior, extension, shared-state, capability, and entitlement diagnostic declared by the task's reviewed acceptance contract
- **THEN** the per-task production publication flag MAY be enabled through reviewed configuration

#### Scenario: Acceptance-relevant contract changes

- **WHEN** the selected source asset, bundle graph, signing policy, or device-level acceptance contract changes
- **THEN** the previous device acceptance SHALL be considered stale for that task
- **AND** production publication SHALL remain disabled until the updated contract is accepted

### Requirement: Debug sessions use least-privilege credentials

An SSH debug step MUST NOT inherit production signing, Apple API, object-storage, revalidation, or repository credentials unless a separately reviewed debug operation explicitly requires an individual credential.

#### Scenario: Operator enables SSH debug

- **WHEN** the workflow starts the public-key-authenticated debug session
- **THEN** production secrets SHALL be absent from the debug process environment
- **AND** decoded private keys, certificates, profiles, and temporary keychains SHALL already be destroyed
- **AND** the checked-out repository SHALL NOT retain an ambient repository token readable during the session
- **AND** the session SHALL retain its authentication, timeout, and audit controls

#### Scenario: Non-production job consumes a credential

- **WHEN** a shadow, probe, or qualification step requires an Apple or repository credential
- **THEN** only that step SHALL receive the required credential
- **AND** unrelated setup, reporting, artifact upload, cleanup, and debug steps SHALL NOT inherit it from job scope

## ADDED Requirements

### Requirement: Secret-safe credential transport

Credentials SHALL be transported only through channels that do not persist them in third-party request logs, URLs, or retained artifacts, and SHALL avoid process-argument exposure wherever the invoked tool supports an environment or file channel.

#### Scenario: Service endpoint requires a shared secret

- **WHEN** the workflow calls an external endpoint that authenticates with a shared secret, such as publication revalidation
- **THEN** the secret SHALL be sent in a request header or body over TLS
- **AND** SHALL NOT appear in the request URL

#### Scenario: Tool requires a private-material password

- **WHEN** a command-line tool needs a certificate or key password
- **THEN** the password SHALL be supplied through an environment or file channel when the tool supports one
- **AND** process-argument exposure SHALL be limited to ephemeral values generated for that run or to documented platform tools with no alternative channel
