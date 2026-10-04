# workflow-optimization Specification

## Purpose
Define rebuild and cache decisions without losing signing-input freshness or publication correctness.

## Requirements

### Requirement: Change Detection Logic
The system SHALL determine signing rebuild work per selected task from its complete signing fingerprint, cache schema, verification evidence and force policy rather than source kind or release version alone.

#### Scenario: Determine signing decisions
- **WHEN** current fingerprints are compared with restored cache records
- **THEN** each selected task SHALL receive a rebuild decision and reason
- **AND** missing records, incompatible schema, changed fingerprint, unverified records or force policy SHALL prevent unchanged reuse

#### Scenario: Reuse unchanged GitHub or direct sources
- **WHEN** a task's complete fingerprint matches its verified record and no force request applies
- **THEN** it MAY reuse the artifact only after current-prerequisite, artifact-identity and retained signing-report checks
- **AND** it SHALL still pass independent verification before publication
- **AND** direct URL source kind alone SHALL NOT force a rebuild

#### Scenario: Source or other signing input changes
- **WHEN** selected source identity/bytes, direct URL/digest, profile/device evidence, policy or tool identity changes the complete fingerprint
- **THEN** the affected selected task SHALL rebuild
- **AND** diagnostics SHALL report changed-input work without exposing secrets

#### Scenario: Operator forces rebuild
- **WHEN** force_rebuild is enabled in the workflow or --force-rebuild is passed to signing
- **THEN** every selected task SHALL rebuild regardless of otherwise reusable cache identity
- **AND** current profile and certificate validation SHALL remain required

#### Scenario: Apparent hit fails reuse checks
- **WHEN** artifact, prerequisite, profile freshness or retained signing-report evidence fails validation
- **THEN** that cache hit SHALL be rejected and the task rebuilt from current validated inputs
- **AND** failed independent output verification SHALL block publication

### Requirement: Conditional Execution
The system SHALL skip signing-backend execution only for validated cache hits, while retaining verification and authorized publication for all selected tasks.

#### Scenario: Restore without re-signing
- **WHEN** a selected task passes signing-cache reuse checks
- **THEN** its content-bound artifact SHALL be restored without signing-backend execution
- **AND** the workflow SHALL continue through independent verification

#### Scenario: Publish verified cache hits
- **WHEN** publication is authorized and a selected task reused its signed artifact
- **THEN** it SHALL still enter the publication transaction
- **AND** unchanged bytes SHALL retain their immutable URL
- **AND** successful revalidation SHALL allow durable retirement cleanup to progress

#### Scenario: Report decisions
- **WHEN** signing finishes for selected tasks
- **THEN** structured decision evidence SHALL identify each task, its rebuild/reuse decision and reason
- **AND** a cache hit SHALL NOT mean its correctness or publication stages were omitted

### Requirement: Cache State Management
The system SHALL maintain a digest-verified signing index and content-bound artifact/report evidence in the package-owned cache and promote only independently verified records.

#### Scenario: Restore durable cache
- **WHEN** the workflow restores signing state
- **THEN** it SHALL restore work/cache through the pinned cache action
- **AND** package code SHALL consume signing-index.json and referenced artifact/report evidence
- **AND** separate release-version and device-list files SHALL NOT be parallel decision authorities

#### Scenario: Missing or malformed index
- **WHEN** no durable signing index exists
- **THEN** selected tasks SHALL require first-run signing
- **WHEN** an existing index has invalid structure or a mismatched digest
- **THEN** its records SHALL be rejected without using them as successful evidence

#### Scenario: Pending signing state
- **WHEN** signing finishes before independent verification
- **THEN** pending records SHALL remain separate from the durable signing index
- **AND** unverified records SHALL NOT qualify as reusable success
- **AND** unrelated verified task records SHALL be preserved

#### Scenario: Promote a verified non-publishing run
- **WHEN** independent verification succeeds and publication is not requested
- **THEN** the package SHALL promote the verified pending index
- **AND** recorded artifact identity SHALL agree with independently verified bytes

#### Scenario: Promote a publishing run
- **WHEN** publication is requested
- **THEN** promotion SHALL be deferred until the publication transaction succeeds
- **AND** verification or publication failure SHALL NOT promote pending records as durable success

#### Scenario: Save cache in CI
- **WHEN** the production job succeeds and reaches cache persistence
- **THEN** it SHALL save work/cache for subsequent runs
- **AND** failed jobs SHALL NOT execute the success-only cache-save step
- **AND** cache persistence failure SHALL NOT justify bypassing verification or promoting incomplete records
