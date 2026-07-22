## MODIFIED Requirements

### Requirement: Safe subprocess invocation

The system SHALL invoke signing tools with explicit argv values, no command shell, bounded execution, bounded captured standard output and error on success and failure, redaction, and typed failure handling.

#### Scenario: Path contains spaces or shell metacharacters

- **WHEN** a certificate, profile, entitlement, input, or output path contains spaces or shell metacharacters
- **THEN** it SHALL be passed as one argv element
- **AND** no part of the value SHALL be evaluated by a shell

#### Scenario: Signing process produces excessive output

- **WHEN** the backend succeeds, fails, or times out after producing more output than the configured evidence bound
- **THEN** retained stdout and stderr SHALL be deterministically truncated and redacted
- **AND** process completion and typed error handling SHALL remain correct

#### Scenario: Signing process times out or exits nonzero

- **WHEN** the backend exceeds its timeout or reports failure
- **THEN** the stage SHALL terminate with bundle/backend context
- **AND** SHALL NOT promote or publish the partial output

### Requirement: Backend provenance

The system SHALL record backend name, version, executable checksum, argv shape with secrets redacted, plan digest, timings, and actual per-node result evidence for every production signing attempt.

#### Scenario: Signing report is generated

- **WHEN** a production signing attempt completes successfully
- **THEN** every planned executable node SHALL have backend evidence containing its signed executable digest, signed entitlement digest, and embedded-profile digest when applicable
- **AND** the report SHALL make the selected backend and affected bundle node traceable
- **AND** SHALL NOT expose P12 passwords, private keys, or raw profile content

#### Scenario: Backend cannot provide complete node evidence

- **WHEN** actual result evidence cannot be collected for any planned node
- **THEN** the production signing stage SHALL fail rather than emitting a successful report with null node evidence
