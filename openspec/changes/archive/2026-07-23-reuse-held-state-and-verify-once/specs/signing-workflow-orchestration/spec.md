## ADDED Requirements

### Requirement: Combined plan-and-apply transaction evidence

A production apply transaction SHALL compute the read-only resource plan it applies and SHALL record both resource-plan and resource-apply stage evidence for the same run and task.

#### Scenario: CI runs one Apple transaction

- **WHEN** CI executes Apple synchronization with apply enabled
- **THEN** the transaction SHALL produce the read-only plan document and record resource-plan evidence before any mutation
- **AND** mutation SHALL proceed only when the plan contains no manual-required or blocked operation

#### Scenario: Plan is blocked inside the transaction

- **WHEN** the computed plan contains a manual-required or blocked operation
- **THEN** the transaction SHALL stop with a non-success status before any mutation
- **AND** downstream stages SHALL fail closed on the missing apply evidence

#### Scenario: Standalone plan mode remains available

- **WHEN** an operator runs plan mode without apply
- **THEN** the same plan document SHALL be emitted without Apple, R2, registry, or cache-success mutation

### Requirement: In-run derived-input reuse

Within one orchestrated production process, immutable derived inputs SHALL be computed once per run and task and consumed by reference or recorded digest; stages executing as separate processes SHALL reconstruct equivalent state from canonical manifests.

#### Scenario: Prepared signing context is reused across stages

- **WHEN** one process executes signing, verification, and publication for a run
- **THEN** configuration parsing, prepared profile and certificate material, the validated signing plan, and backend identity SHALL be produced once per task
- **AND** later stages SHALL consume the same instances or their recorded digests instead of recomputing them

#### Scenario: Stage processes rely on canonical manifests

- **WHEN** production stages execute as separate processes
- **THEN** each stage SHALL reconstruct required inputs by loading and validating the canonical predecessor manifests for the same run and task

### Requirement: Debug sessions receive production credentials on explicit request

An SSH debug session SHALL start only from a manually dispatched workflow run with the debug input enabled and public-key authentication, and the production workflow's debug step SHALL receive the production credential set as step-scoped environment so live Apple, signing, and publication behavior can be exercised in the real CI environment.

#### Scenario: Operator enables production debug

- **WHEN** the production workflow is manually dispatched with the debug input enabled and the public-key-authenticated debug session starts
- **THEN** the debug step SHALL receive the production credential set as step-scoped environment variables
- **AND** the SSH server SHALL preserve that environment for debug child processes
- **AND** the session SHALL retain its public-key authentication, timeout, and audit controls

#### Scenario: Debug is not requested

- **WHEN** a workflow runs without the explicit debug input
- **THEN** no debug session or tunnel SHALL start
- **AND** credentials SHALL reach only the production steps that require them

#### Scenario: Production step consumes a credential

- **WHEN** a production step requires an Apple, repository, publication, revalidation, or notification credential
- **THEN** only that step SHALL receive the required credential
- **AND** unrelated setup, reporting, and cache steps SHALL NOT inherit it from job scope

#### Scenario: PR validation debug stays credential-free

- **WHEN** the PR validation workflow starts its explicitly requested debug session
- **THEN** production signing, Apple API, object-storage, revalidation, and notification credentials SHALL NOT be present in that session's environment

#### Scenario: Debug session output is retained

- **WHEN** a debug session or its parent workflow retains logs or artifacts
- **THEN** credential values SHALL NOT be printed or persisted into retained artifacts

## REMOVED Requirements

### Requirement: Debug sessions use least-privilege credentials

**Reason**: A credential-free debug session cannot exercise live App Store Connect, signing, or publication behavior, which is the purpose of debugging the production workflow in its real environment. The operator accepts step-scoped production credentials inside explicitly requested, public-key-authenticated production debug sessions.

**Migration**: The production workflow passes the production credential set to the debug step as step-scoped environment and the SSH server preserves it for the session. Job-level environment still carries no secrets, non-debug production steps keep per-step credential injection, the PR validation debug session remains credential-free, and retained logs and artifacts keep their redaction rules. These properties are enforced by `tests/test_workflow_toolchain.py`.

## MODIFIED Requirements

### Requirement: Cache never bypasses correctness gates

The system SHALL treat cached data as an optimization and SHALL revalidate time-sensitive or security-sensitive evidence before reuse or publication.

#### Scenario: Reuse a cached signed artifact

- **WHEN** a cache fingerprint matches
- **THEN** current profile validity/manual-prerequisite status SHALL be checked and the cached artifact digest SHALL match the cache record at the reuse decision
- **AND** the cached IPA SHALL be reopened and pass the complete verification gate exactly once within the same run before cache promotion or publication

#### Scenario: Run fails after cache restore

- **WHEN** any stage fails after cached state is loaded
- **THEN** the system SHALL NOT write a successful stage marker or replace the last known verified manifest for that task

### Requirement: Production cache-hit verification

The production orchestrator MUST use complete per-task cache fingerprints and MUST treat every cache hit as untrusted until current prerequisite revalidation and the run's full artifact verification succeed.

#### Scenario: Production task fingerprint matches

- **WHEN** a production task has a matching cached fingerprint
- **THEN** the workflow SHALL revalidate current profile dates, devices, certificate and prerequisite status, and the cached artifact digest at the reuse decision
- **AND** the cached IPA SHALL pass the run's single complete independent verification before cache promotion or publication

#### Scenario: Cache evidence is stale or invalid

- **WHEN** current prerequisites, artifact digest, plan digest, or verification evidence differs from the cache record
- **THEN** the task SHALL be rebuilt or fail closed according to the reported cause
- **AND** the stale cache record SHALL NOT be promoted as successful
