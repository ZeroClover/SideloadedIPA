## ADDED Requirements

### Requirement: Canonical source and inventory manifest reuse
The production pipeline MUST persist one canonical source-selection and unsigned-inventory evidence chain per run and task and MUST make downstream stages validate and consume that chain.

#### Scenario: Inspect a selected task
- **WHEN** source resolution, bounded download, and safe inventory succeed
- **THEN** the pipeline SHALL atomically persist schema-versioned source and inventory manifests
- **AND** the evidence SHALL bind run ID, task, resolved source identity, expected and actual digest, expected and actual size, graph digest, and predecessor status

#### Scenario: Advance a downstream stage
- **WHEN** plan, sync, or sign executes for the same run and task
- **THEN** the stage SHALL load and validate the canonical predecessor manifests
- **AND** it SHALL construct typed inputs without resolving, downloading, extracting, or inventorying the unchanged unsigned source again

#### Scenario: Canonical input evidence is missing or changed
- **WHEN** a manifest is absent, malformed, for another run/task, has an unsupported schema, or no longer matches source bytes and predecessor digests
- **THEN** the requested stage SHALL fail closed
- **AND** no Apple mutation, signing, cache promotion, or publication SHALL occur

#### Scenario: Verify signed output
- **WHEN** signing produces an output IPA or a cache hit is considered for reuse
- **THEN** the independent verifier SHALL reopen and inventory the output artifact
- **AND** unsigned-input manifest reuse SHALL NOT substitute for graph, profile, entitlement, signature, or package verification

### Requirement: Atomic pipeline decision evidence
Pipeline decision and stage-report files SHALL use the same atomic persistence boundary as canonical stage manifests.

#### Scenario: Persist a cache or stage decision
- **WHEN** the pipeline records rebuild, cache, cancellation, or stage-result evidence
- **THEN** it SHALL write canonical bytes to a task-scoped temporary path, flush them, and atomically promote the complete file
- **AND** a process interruption SHALL NOT expose a partially written successful decision

### Requirement: Transaction-scoped Apple profile state reuse
Apple profile synchronization MUST reuse one normalized account profile snapshot across all selected bundle targets in the transaction without weakening per-profile validation or mutation verification.

#### Scenario: Reconcile multiple existing profiles
- **WHEN** one apply transaction reconciles profiles for multiple bundle targets
- **THEN** it SHALL enumerate and normalize the account profile collection once for those targets
- **AND** it SHALL download and validate every selected profile independently before storing or signing with it

#### Scenario: Reconcile prerequisites without changing profiles
- **WHEN** App ID and capability reconciliation does not create or replace a profile
- **THEN** intermediate Apple state refreshes SHALL reuse the transaction's normalized profile collection
- **AND** they SHALL NOT enumerate every profile again

#### Scenario: Create or recover a profile
- **WHEN** a profile is created or a create result is uncertain
- **THEN** synchronization SHALL verify or recover the exact created resource against current remote relationships
- **AND** refreshed profile state SHALL be used for later targets and final canonical evidence

#### Scenario: Reused profile content changed or cannot be read
- **WHEN** downloading a selected profile fails or its bytes no longer match the normalized snapshot
- **THEN** synchronization SHALL fail closed or create a validated additive replacement according to the existing reconciliation policy
- **AND** stale snapshot evidence SHALL NOT be stored as a successful profile manifest
