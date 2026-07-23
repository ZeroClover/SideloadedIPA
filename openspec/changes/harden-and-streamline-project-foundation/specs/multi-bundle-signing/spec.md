## ADDED Requirements

### Requirement: Backend requalification lifecycle
The repository MUST retain a minimal repeatable qualification contract for every signing backend used to apply distinct profiles and entitlements across a bundle graph.

#### Scenario: Backend contract changes
- **WHEN** the backend version, executable digest, patch set, command shape, entitlement application behavior, or supported platform changes
- **THEN** the backend SHALL be requalified before the new identity is accepted for production
- **AND** the resulting evidence SHALL identify the fixture, backend, plan, output, and independent comparison digests

#### Scenario: Pull-request validation exercises the backend
- **WHEN** consolidated PR validation runs with the reviewed patched backend
- **THEN** it SHALL execute the deterministic multi-bundle fixture through the real production backend adapter
- **AND** it SHALL prove distinct per-bundle profiles and entitlements plus a tamper or unsigned failure case

#### Scenario: Operator performs independent oracle comparison
- **WHEN** a backend-affecting change requires the macOS codesign oracle
- **THEN** one documented qualification entry point SHALL produce redacted comparable evidence for patched-backend and oracle outputs
- **AND** absence of the required macOS environment SHALL be reported as an unmet manual qualification gate rather than a passing result

#### Scenario: Qualification needs Apple resources
- **WHEN** the qualification plan requires current App IDs, capabilities, App Groups, or profiles
- **THEN** the entry point SHALL reuse the production inspect, plan, and synchronization primitives
- **AND** a separate migration-only resource mutation or destructive reset implementation SHALL NOT decide production readiness

#### Scenario: Legacy qualification utility has no supported caller
- **WHEN** repository, workflow, runbook, and operator-entry-point searches prove that a qualification wrapper only duplicates the retained contract or production primitives
- **THEN** that utility and tests that exist only for its duplicate behavior SHALL be removed together
- **AND** the deterministic fixture, production adapter test, oracle comparison, and retained qualification evidence SHALL remain supported
