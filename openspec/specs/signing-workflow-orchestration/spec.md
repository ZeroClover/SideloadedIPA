# signing-workflow-orchestration Specification

## Purpose
Define the staged production workflow, canonical evidence chain, cache safety boundaries, retained reports, and fail-closed publication behavior for IPA signing runs.
## Requirements
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

### Requirement: Read-only inspect and plan modes

The system SHALL expose inspect and plan operations that can produce bundle, entitlement, identifier, capability, profile, and manual-action reports without signing or mutating remote state.

#### Scenario: Operator plans multi-bundle prerequisites

- **WHEN** the operator runs plan mode with valid read credentials and a multi-bundle task configuration
- **THEN** the report SHALL enumerate all required target App IDs, profiles, capabilities, App Groups, and human prerequisites
- **AND** SHALL make no Apple, R2, registry, or cache-success mutation

#### Scenario: Plan detects manual work

- **WHEN** a required App Group, managed capability approval, sensitive entitlement authorization, agreement, or role is absent
- **THEN** plan mode SHALL return a non-success readiness status with actionable steps
- **AND** SHALL NOT misclassify the task as ready to sign

### Requirement: Complete cache fingerprint

The system SHALL calculate task/stage cache identities from every input that can affect resource validity, signed bytes, verification, or publication semantics.

#### Scenario: Calculate a signing cache key

- **WHEN** a signing plan is complete
- **THEN** its cache fingerprint SHALL include source asset identity and digest, configuration/policy digest, bundle-graph digest, entitlement-template digests, target identifiers, Apple resource/profile fingerprints and expiry, certificate fingerprint, device digest, backend/tool versions, and pipeline schema version

#### Scenario: Relevant input changes

- **WHEN** any fingerprint input changes
- **THEN** the affected cached signing result SHALL be invalidated
- **AND** unrelated tasks MAY remain reusable when their fingerprints are unchanged

### Requirement: Cache never bypasses correctness gates

The system SHALL treat cached data as an optimization and SHALL revalidate time-sensitive or security-sensitive evidence before reuse or publication.

#### Scenario: Reuse a cached signed artifact

- **WHEN** a cache fingerprint matches
- **THEN** current profile validity/manual-prerequisite status SHALL be checked
- **AND** the cached IPA SHALL be reopened and pass the publication verification gate

#### Scenario: Run fails after cache restore

- **WHEN** any stage fails after cached state is loaded
- **THEN** the system SHALL NOT write a successful stage marker or replace the last known verified manifest for that task

### Requirement: Atomic verified publication

The system SHALL preserve the last verified published artifact and registry entry until a newly verified artifact is ready for promotion.

#### Scenario: New artifact verifies and uploads

- **WHEN** a new immutable object is uploaded and its digest is confirmed
- **THEN** registry mutation SHALL reference that verified object
- **AND** revalidation and stale-object cleanup SHALL occur only after the registry update succeeds

#### Scenario: Signing, verification, or upload fails

- **WHEN** any pre-publication stage fails
- **THEN** the previous registry entry and object SHALL remain active
- **AND** no partial result SHALL be advertised

#### Scenario: One selected task fails in a batch

- **WHEN** workflow policy requires batch-atomic publication and one selected task fails
- **THEN** registry mutation for the batch SHALL be skipped
- **AND** each task's independent verification result SHALL still be reported

### Requirement: Structured diagnostics and provenance

The system SHALL emit concise human output and a schema-versioned redacted JSON run report across local and CI execution.

#### Scenario: Run report is complete

- **WHEN** a run finishes
- **THEN** the report SHALL include measured stage status/timing, source release and digest, graph and plan digests, bundle mappings, capability classifications, manual actions, Apple resource IDs, non-secret certificate/profile/tool fingerprints, verification results, cache decisions, and publication outcome
- **AND** timing that cannot be observed at the represented granularity SHALL be null or omitted rather than reported as a fabricated zero

#### Scenario: Secret appears in adapter output

- **WHEN** a subprocess or API error includes a known credential or secret path/value
- **THEN** log/report handling SHALL redact it before display or artifact retention

### Requirement: Safe retry and cleanup boundaries

The system SHALL make read and additive idempotent stages retryable while preventing automatic repetition of ambiguous or destructive operations.

#### Scenario: Retry a transient read or upload

- **WHEN** an official API read or content-addressed upload fails transiently
- **THEN** the adapter MAY retry with bounded exponential backoff and jitter
- **AND** SHALL preserve the same operation identity

#### Scenario: Workflow is cancelled

- **WHEN** a run is cancelled during inspection, signing, or verification
- **THEN** task-scoped temporary data SHALL be cleaned where safe
- **AND** Apple resources already created SHALL be recorded but not deleted
- **AND** publication state SHALL remain unchanged unless its transaction had already completed

### Requirement: Pinned and verified external tools

The workflow SHALL install supported zsign and App Store Connect CLI releases from their current canonical repositories and SHALL verify published checksums and runtime versions.

#### Scenario: Install a supported tool release

- **WHEN** CI downloads a configured tool asset
- **THEN** it SHALL verify the asset checksum before execution
- **AND** SHALL record the canonical repository, version, and executable digest

#### Scenario: Checksum or version verification fails

- **WHEN** the downloaded bytes or runtime version differ from configuration
- **THEN** the workflow SHALL stop before credentials, Apple mutations, or signing are attempted

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

### Requirement: Production orchestration authority

The production CLI and CI workflow MUST execute source, inventory, policy preflight, Apple plan/apply, signing plan, signing, verification, and publication through the package-owned manifest orchestration service.

#### Scenario: Production advances between visible stages

- **WHEN** CI advances a selected task to a mutating or publishing stage
- **THEN** the command SHALL load and validate the canonical predecessor manifest for the same run and task
- **AND** legacy scripts, ad-hoc environment outputs, and fixture-only operations SHALL NOT decide stage readiness or rebuild selection

#### Scenario: Predecessor evidence is absent or changed

- **WHEN** a stage cannot validate its predecessor manifest or recomputed input digest
- **THEN** that stage and every downstream side effect SHALL fail closed

### Requirement: Production cache-hit verification

The production orchestrator MUST use complete per-task cache fingerprints and MUST treat every cache hit as untrusted until current prerequisite and full artifact verification succeeds.

#### Scenario: Production task fingerprint matches

- **WHEN** a production task has a matching cached fingerprint
- **THEN** the workflow SHALL revalidate current profile dates, devices, certificate and prerequisite status
- **AND** SHALL reopen the cached IPA with the complete independent verifier before reuse or publication

#### Scenario: Cache evidence is stale or invalid

- **WHEN** current prerequisites, artifact digest, plan digest, or verification evidence differs from the cache record
- **THEN** the task SHALL be rebuilt or fail closed according to the reported cause
- **AND** the stale cache record SHALL NOT be promoted as successful

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
- **WHEN** a manually dispatched production or PR validation workflow starts the public-key-authenticated debug session
- **THEN** production secrets SHALL be absent from the debug process environment
- **AND** decoded private keys, certificates, profiles, and temporary keychains SHALL already be destroyed or inaccessible to the debug processes
- **AND** the checked-out repository SHALL NOT retain an ambient repository token readable during the session
- **AND** the session SHALL retain its authentication, timeout, and audit controls

#### Scenario: Production step consumes a credential
- **WHEN** a production step requires an Apple, repository, publication, revalidation, or notification credential
- **THEN** only that step SHALL receive the required credential
- **AND** unrelated setup, reporting, cache, and debug steps SHALL NOT inherit it from job scope

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
