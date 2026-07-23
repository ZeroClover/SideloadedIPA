## ADDED Requirements

### Requirement: Bounded HTTPS asset transport
The system MUST download a selected GitHub release asset over HTTPS within a package-owned resource policy before inventory or signing can begin.

#### Scenario: Selected asset uses HTTPS
- **WHEN** GitHub returns the selected asset download URL
- **THEN** the downloader SHALL require an HTTPS URL with a valid authority
- **AND** redirects SHALL NOT downgrade the transfer to an insecure scheme

#### Scenario: Declared content length exceeds the limit
- **WHEN** the HTTP response declares a byte length greater than the reviewed source limit
- **THEN** the download SHALL fail before the response body is written
- **AND** the diagnostic SHALL report declared and allowed bytes

#### Scenario: Stream exceeds the limit without a usable length
- **WHEN** the streamed bytes exceed the reviewed source limit
- **THEN** the downloader SHALL stop immediately
- **AND** the temporary file SHALL be removed without exposing a source artifact

### Requirement: Release asset evidence verification
The system SHALL reconcile the downloaded bytes with the selected GitHub asset's advertised identity, size, and available digest.

#### Scenario: Advertised size matches
- **WHEN** an asset download completes
- **THEN** its actual byte count SHALL equal the selected asset's advertised size
- **AND** both values SHALL be retained as source evidence

#### Scenario: Advertised size differs
- **WHEN** the actual byte count differs from the selected asset's advertised size
- **THEN** source intake SHALL fail before inventory
- **AND** no cache-success or publication state SHALL change

#### Scenario: GitHub advertises a SHA-256 digest
- **WHEN** the selected asset includes digest evidence
- **THEN** the downloaded SHA-256 SHALL match it exactly
- **AND** a mismatch SHALL fail closed

#### Scenario: GitHub does not advertise a digest
- **WHEN** the selected asset has no digest field
- **THEN** the system SHALL still calculate and retain the actual SHA-256 with the asset ID, URL, and size
- **AND** downstream stages SHALL bind to that measured digest for the current run

### Requirement: Identity-preserving source retries
The system SHALL retry a transient asset read only within a bounded policy and only while the resolved release and asset identity remain unchanged.

#### Scenario: Idempotent download fails transiently
- **WHEN** the same selected asset encounters a retryable transport or server failure before successful completion
- **THEN** the downloader SHALL use bounded backoff and a fresh temporary file
- **AND** every attempt SHALL retain the same release tag, asset ID, URL, expected size, and expected digest

#### Scenario: Asset identity changes during retry
- **WHEN** a retry would require resolving a different release, asset ID, URL, size, or digest
- **THEN** the current operation SHALL fail
- **AND** a new inspect run SHALL be required

#### Scenario: Retry budget is exhausted
- **WHEN** all permitted attempts fail
- **THEN** source intake SHALL return one bounded diagnostic with the attempt count
- **AND** no partial source file SHALL remain
