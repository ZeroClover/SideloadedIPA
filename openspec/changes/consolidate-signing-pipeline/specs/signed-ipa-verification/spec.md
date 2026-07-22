## MODIFIED Requirements

### Requirement: Fail-closed verification report and publication gate

The system SHALL emit schema-versioned per-bundle verification findings and SHALL allow publication only when every required check passes.

#### Scenario: Verification succeeds

- **WHEN** identity, profile, entitlement, signature, graph, and package checks all pass
- **THEN** the report SHALL mark the artifact verified and record evidence hashes
- **AND** orchestration MAY proceed to publication

#### Scenario: Verification has a warning or failure

- **WHEN** a required check fails or evidence cannot be obtained
- **THEN** the artifact SHALL NOT be marked verified
- **AND** upload, registry mutation, revalidation, and stale-object deletion SHALL be blocked

#### Scenario: Report is retained

- **WHEN** verification completes
- **THEN** human-readable and redacted JSON summaries SHALL identify every bundle and failed contract
- **AND** raw private profiles, certificate material, and secrets SHALL be excluded

#### Scenario: Assembled composition uses the production verifier

- **WHEN** automated tests exercise the sign-then-verify composition
- **THEN** at least one test SHALL run the production verifier against genuine signature evidence produced within the test
- **AND** a companion negative test SHALL prove an unsigned or tampered artifact is not marked verified and is not promoted
- **AND** always-passing verifier stand-ins SHALL NOT be the only automated coverage of the composition
