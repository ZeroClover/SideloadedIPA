## ADDED Requirements

### Requirement: Consolidated pull-request validation
Pull-request and manually dispatched CI SHALL execute Python, configuration, signing-backend, workflow, composite-action, and web validation in one reproducible job.

#### Scenario: Validate a repository change
- **WHEN** PR validation runs
- **THEN** it SHALL install the locked Python and web dependencies
- **AND** SHALL execute pytest with its coverage gate, formatting checks, strict package and script typing, real patched-zsign coverage, web tests, and the production web build
- **AND** a failure in any validation boundary SHALL fail the job

#### Scenario: Configuration examples change
- **WHEN** production or example task configuration changes
- **THEN** the normal Python test suite SHALL parse and validate both files
- **AND** CI SHALL NOT maintain a duplicate command-only parsing job

### Requirement: GitHub Actions static analysis
CI SHALL validate workflow files and composite action definitions with tools that understand their respective formats and security properties.

#### Scenario: Workflow or composite action changes
- **WHEN** PR validation analyzes `.github`
- **THEN** actionlint SHALL validate workflow syntax, expressions, and embedded shell
- **AND** pinned zizmor SHALL strictly collect and analyze workflows and composite actions
- **AND** high-severity findings SHALL fail CI
- **AND** a generic YAML object-shape check SHALL NOT substitute for Actions-aware analysis

#### Scenario: External Action is referenced
- **WHEN** a workflow or composite action uses a third-party Action
- **THEN** the reference SHALL be pinned to a full immutable commit digest
- **AND** a human-readable release version SHALL remain documented beside the digest

### Requirement: Debuggable validation environment
Manual PR validation SHALL retain one public-key-authenticated SSH debug entry point in the same runner environment that performed all checks.

#### Scenario: Operator enables PR debug
- **WHEN** a manually dispatched validation run sets `debug=true`
- **THEN** the SSH step SHALL run after all validation steps even if an earlier step failed
- **AND** the operator SHALL enter the checkout and tool environment used by Python, Actions, and web validation
- **AND** CI SHALL NOT start multiple competing SSH sessions for one validation run

### Requirement: Minimal recurring CI surface
The repository SHALL use recurring automation only for PR validation and the production signing schedule.

#### Scenario: Checksum-pinned external fixture needs inspection
- **WHEN** an operator needs to re-run the historical real-IPA inventory contract
- **THEN** the opt-in integration test MAY be run explicitly in a local or deliberately provisioned environment
- **AND** the repository SHALL NOT maintain a standalone scheduled integration workflow solely for that fixture
