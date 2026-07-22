# signing-workflow-orchestration Specification

## Purpose
Define the staged production workflow, canonical evidence chain, cache safety boundaries, retained reports, and fail-closed publication behavior for IPA signing runs.
## Requirements
### Requirement: Ordered staged workflow

The system SHALL coordinate source resolution, safe inventory, configuration matching, Apple resource planning/apply, signing-plan validation, signing, output verification, and publication as explicit ordered stages.

#### Scenario: Run a task successfully

- **WHEN** every stage completes successfully and no manual prerequisite remains
- **THEN** each downstream stage SHALL consume the typed manifest from its predecessor
- **AND** publication SHALL occur only after verification

#### Scenario: An early stage fails

- **WHEN** source selection, inventory, configuration, or resource planning fails
- **THEN** all later mutation stages SHALL be skipped
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
- **THEN** the report SHALL include stage status/timing, source release and digest, graph and plan digests, bundle mappings, capability classifications, manual actions, Apple resource IDs, non-secret certificate/profile/tool fingerprints, verification results, cache decisions, and publication outcome

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

The system SHALL retain current operational entry points through thin compatibility wrappers until all configured tasks pass parity acceptance on the package engine.

#### Scenario: Existing workflow calls a legacy script path

- **WHEN** migration still supports `scripts/run_signing.py` or `scripts/sync_profiles_asc.py`
- **THEN** the wrapper SHALL delegate to package use cases without duplicating business rules
- **AND** SHALL preserve documented environment inputs and exit behavior or provide an explicit migration diagnostic

#### Scenario: Parity is not yet accepted

- **WHEN** a legacy single-bundle task differs in source selection, output identity, cache, registry, or failure behavior under the new engine
- **THEN** the legacy switch SHALL remain available for that task
- **AND** wrapper removal SHALL be blocked

### Requirement: Production acceptance for new multi-bundle tasks

The system SHALL keep a newly enabled multi-bundle task non-publishing until automated verification and the task's reviewed registered-device acceptance contract both pass for the selected source asset.

#### Scenario: Automated canary passes but device acceptance is absent

- **WHEN** a new multi-bundle task passes inventory, resource, signing, and verification gates but has no current physical-device acceptance record
- **THEN** the canary artifact MAY be retained privately for testing
- **AND** production registry publication SHALL remain disabled

#### Scenario: Complete physical-device acceptance

- **WHEN** an operator records successful completion of every behavior, extension, shared-state, capability, and entitlement diagnostic declared by the task's reviewed acceptance contract
- **THEN** the per-task production publication flag MAY be enabled through reviewed configuration

#### Scenario: Acceptance-relevant contract changes

- **WHEN** the selected source asset, bundle graph, signing policy, or device-level acceptance contract changes
- **THEN** the previous device acceptance SHALL be considered stale for that task
- **AND** production publication SHALL remain disabled until the updated contract is accepted
