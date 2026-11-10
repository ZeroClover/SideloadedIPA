# ci-validation Specification

## Purpose
Define reproducible PR/manual validation gates, workflow security analysis and the supported CI surface.
## Requirements
### Requirement: Consolidated pull-request validation
Pull-request and manually dispatched CI SHALL execute Python, workflow, composite-action, and web static validation in one reproducible job, and SHALL execute every test suite in a separate test workflow.

#### Scenario: Validate a repository change
- **WHEN** PR validation runs
- **THEN** it SHALL install the locked Python and web dependencies
- **AND** SHALL execute dependency audits, formatting checks, strict package and script typing, the Python distribution build, and the production web build
- **AND** SHALL NOT execute test suites
- **AND** a failure in any validation boundary SHALL fail the job

#### Scenario: Run the test suites
- **WHEN** a pull request targets the default branch or the test workflow is manually dispatched
- **THEN** the test workflow SHALL execute pytest with its coverage gate and real patched-zsign coverage, the pipeline end-to-end suite, the web unit tests, and the web end-to-end suite
- **AND** it SHALL require no repository secret
- **AND** a failure in any suite SHALL fail the workflow

#### Scenario: Configuration examples change
- **WHEN** production or example task configuration changes
- **THEN** the normal Python test suite SHALL pass both files through production validation
- **AND** it SHALL NOT assert the configured values of individual tasks
- **AND** CI SHALL NOT maintain a duplicate command-only parsing job

### Requirement: Behavioural tests and end-to-end coverage
Unit tests SHALL NOT pin production task configuration, workflow definitions, or project metadata; the composed pipeline and the delivery site SHALL each be covered by an end-to-end suite that enters through the interface an operator or client uses.

#### Scenario: Production task configuration changes
- **WHEN** a production task is added, removed, or edited, including its publication flag, signing rules, or entitlement template
- **THEN** no unit test SHALL fail because it asserted or borrowed the previous production values
- **AND** unit tests SHALL take sample tasks and entitlement templates from test-owned fixtures

#### Scenario: Workflow or project metadata changes
- **WHEN** a workflow definition or project metadata field changes
- **THEN** workflow-aware static analysis and the build SHALL be the gates
- **AND** no unit test SHALL assert the text of those files

#### Scenario: Pipeline end-to-end suite runs
- **WHEN** the pipeline end-to-end suite executes
- **THEN** it SHALL run the installed command-line entry point in a child process through source intake, inventory, Apple reconciliation, signing with the real patched backend, and independent verification
- **AND** it SHALL replace only unreachable external services, at a process or network boundary, without test-only production configuration
- **AND** it SHALL prove that a signed artifact modified after signing is not verified
- **AND** a signing scenario SHALL fail rather than skip when the patched backend is unavailable

#### Scenario: Delivery end-to-end suite runs
- **WHEN** the web end-to-end suite executes
- **THEN** it SHALL serve a production build over HTTP against a registry origin
- **AND** SHALL cover the application page, the installation manifest, and secret-gated registry revalidation

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
Manual PR validation SHALL retain one public-key-authenticated SSH debug entry point in the same runner environment that performed the static validation.

#### Scenario: Operator enables PR debug
- **WHEN** a manually dispatched validation run sets `debug=true`
- **THEN** the SSH step SHALL run after all validation steps even if an earlier step failed
- **AND** the operator SHALL enter the checkout and tool environment used by Python, Actions, and web validation
- **AND** CI SHALL NOT start multiple competing SSH sessions for one validation run

### Requirement: Minimal recurring CI surface
The repository SHALL use recurring automation only for PR validation, the PR test workflow, and the production signing schedule.

#### Scenario: Checksum-pinned external fixture needs inspection
- **WHEN** an operator needs to re-run the historical real-IPA inventory contract
- **THEN** the opt-in integration test MAY be run explicitly in a local or deliberately provisioned environment
- **AND** the repository SHALL NOT maintain a standalone scheduled integration workflow solely for that fixture

### Requirement: Test-double protocol conformance
Test doubles that stand in for the signing backend or package verifier in pipeline tests SHALL pass the same protocol-conformance contract tests as the production implementations, executed in the ordinary pull-request test suite without credentials or external signing binaries.

#### Scenario: Double used in a pipeline test
- **WHEN** a fixture backend or verifier replaces the production implementation in a test
- **THEN** the contract test suite SHALL assert it conforms to the same port protocol and call-signature shape as the production adapter
- **AND** the double SHALL live in a dedicated test-support module naming the conformance gate

#### Scenario: Port protocol changes
- **WHEN** the signing backend or verifier protocol gains or changes a member
- **THEN** the contract tests SHALL fail until the production adapters and all test doubles are updated together
