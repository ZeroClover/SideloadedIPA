## ADDED Requirements

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

- **WHEN** execution is interrupted after local or remote side effects begin
- **THEN** temporary work SHALL be cleaned where safe
- **AND** created Apple resource identities, publication commit state, and unresolved cleanup actions SHALL be recorded without secrets

### Requirement: Compensating cleanup for failed publication

The publication transaction SHALL remove newly uploaded immutable objects that are not referenced after the transaction fails.

#### Scenario: Batch upload or registry promotion fails

- **WHEN** one or more new objects were uploaded but the batch registry was not successfully promoted and revalidated
- **THEN** the previous registry SHALL remain or be restored
- **AND** the gateway SHALL attempt deletion of only the unreferenced keys uploaded by that attempt
- **AND** any cleanup failure SHALL report the remaining keys without masking the original publication failure

### Requirement: Debug sessions use least-privilege credentials

An SSH debug step MUST NOT inherit production signing, Apple API, object-storage, revalidation, or repository credentials unless a separately reviewed debug operation explicitly requires an individual credential.

#### Scenario: Operator enables SSH debug

- **WHEN** the workflow starts the public-key-authenticated debug session
- **THEN** production secrets SHALL be absent from the debug process environment
- **AND** the session SHALL retain its authentication, timeout, and audit controls
