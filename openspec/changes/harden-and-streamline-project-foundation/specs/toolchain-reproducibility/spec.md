## ADDED Requirements

### Requirement: Explicit supported runtime toolchain
The repository SHALL declare and install the Python, Node.js, and uv versions used by local development and consolidated CI validation instead of selecting an arbitrary compatible runtime.

#### Scenario: Create a local Python environment
- **WHEN** uv resolves the repository's default Python interpreter
- **THEN** it SHALL use the reviewed repository version declaration
- **AND** that version SHALL satisfy the package's supported-Python contract

#### Scenario: Run consolidated CI validation
- **WHEN** the PR validation job installs Python, Node.js, and uv
- **THEN** it SHALL install the reviewed explicit versions
- **AND** the versions SHALL match the repository declarations before dependency installation

#### Scenario: Toolchain version is updated
- **WHEN** a runtime or uv version changes
- **THEN** the change SHALL update every local and CI declaration together
- **AND** the complete Python, web, workflow, and OpenSpec acceptance stack SHALL pass

### Requirement: Frozen dependency installation
Validation and production automation MUST consume committed lockfiles without silently resolving or rewriting them.

#### Scenario: Install Python dependencies in CI
- **WHEN** CI prepares the Python environment
- **THEN** uv SHALL perform a frozen or locked exact synchronization from `uv.lock`
- **AND** a stale or missing lockfile SHALL fail validation

#### Scenario: Install web dependencies in CI
- **WHEN** CI prepares the web environment
- **THEN** npm SHALL install from `package-lock.json` using `npm ci`
- **AND** package lifecycle behavior SHALL remain explicit and reviewed

#### Scenario: Repository no longer supports pre-3.11 Python
- **WHEN** the dependency manifest is validated
- **THEN** it SHALL NOT retain dependencies guarded exclusively for Python versions below the declared minimum

### Requirement: Reviewed dependency security gate
The repository MUST audit the committed Python and npm dependency graphs and MUST NOT accept an unreviewed high- or critical-severity advisory.

#### Scenario: Locked dependencies have no blocking findings
- **WHEN** PR validation audits the frozen Python lock and npm lock
- **THEN** the audit commands SHALL succeed
- **AND** their dependency scope SHALL be visible in the validation output

#### Scenario: A fixed vulnerable version is present
- **WHEN** a high- or critical-severity finding has a compatible fixed version
- **THEN** validation SHALL fail until the lock is updated and the acceptance stack passes

#### Scenario: Upstream has no compatible fix
- **WHEN** a blocking advisory cannot yet be remediated compatibly
- **THEN** any temporary exception SHALL identify the advisory, affected dependency path, reachability, owner, remediation condition, and expiry
- **AND** a blanket, ownerless, or unbounded ignore SHALL be rejected

#### Scenario: An ignored advisory becomes fixable
- **WHEN** the audit source reports a fixed version for an advisory that was temporarily ignored only until fixed
- **THEN** the security gate SHALL fail again
- **AND** the dependency SHALL be upgraded or a new reviewed exception SHALL be required

### Requirement: Managed dependency updates
The repository SHALL use supported dependency-update automation for uv, npm, and GitHub Actions while retaining human review and immutable workflow references.

#### Scenario: An update is available
- **WHEN** the updater opens a dependency change
- **THEN** it SHALL update the relevant manifest and lock or immutable Action digest together
- **AND** the pull request SHALL run the consolidated validation job

#### Scenario: External GitHub Action is updated
- **WHEN** an Action release is accepted
- **THEN** its workflow reference SHALL remain a full immutable commit digest
- **AND** the adjacent human-readable version comment SHALL be synchronized

### Requirement: Proportional coverage artifacts
The default developer test command SHALL enforce the package coverage contract without generating a persistent HTML report unless the operator explicitly requests it.

#### Scenario: Run normal local or CI tests
- **WHEN** the default pytest command executes
- **THEN** it SHALL report missing coverage in the terminal and enforce the configured threshold
- **AND** it SHALL NOT generate the HTML coverage directory

#### Scenario: Developer requests an HTML report
- **WHEN** the documented coverage-diagnostics command runs
- **THEN** it SHALL generate the HTML report from the same measured package scope
