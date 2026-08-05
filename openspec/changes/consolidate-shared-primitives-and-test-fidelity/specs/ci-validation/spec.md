## ADDED Requirements

### Requirement: Test-double protocol conformance
Test doubles that stand in for the signing backend or package verifier in pipeline tests SHALL pass the same protocol-conformance contract tests as the production implementations, executed in the ordinary pull-request test suite without credentials or external signing binaries.

#### Scenario: Double used in a pipeline test
- **WHEN** a fixture backend or verifier replaces the production implementation in a test
- **THEN** the contract test suite SHALL assert it conforms to the same port protocol and call-signature shape as the production adapter
- **AND** the double SHALL live in a dedicated test-support module naming the conformance gate

#### Scenario: Port protocol changes
- **WHEN** the signing backend or verifier protocol gains or changes a member
- **THEN** the contract tests SHALL fail until the production adapters and all test doubles are updated together

### Requirement: No test-only public production code
A public function in the production package SHALL be reachable from a production entry point, be an explicitly registered console-script entry point, or be removed; tests SHALL NOT be the sole caller of production code.

#### Scenario: Audit finds a test-only public function
- **WHEN** a static reachability check finds a public production function referenced only from tests
- **THEN** CI SHALL fail
- **AND** the function SHALL be deleted, wired into a production path, or moved to test support with its tests
