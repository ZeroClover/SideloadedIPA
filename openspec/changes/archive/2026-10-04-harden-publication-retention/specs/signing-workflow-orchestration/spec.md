## ADDED Requirements

### Requirement: Durable retired artifact retention

The publisher SHALL retain managed IPA and icon objects for at least 48 hours after first observing them unreferenced, and SHALL persist retirement state across runs.

#### Scenario: Replace an old published artifact
- **WHEN** a successful publication replaces an artifact regardless of its upload age
- **THEN** the current and previous registry references SHALL survive that publication sweep
- **AND** subsequent unreferenced observations SHALL start a fresh retirement grace period

#### Scenario: Complete retirement on a later scheduled run
- **WHEN** an object remains unreferenced for at least 48 hours and revalidation succeeds
- **THEN** cleanup SHALL remove it even when selected artifacts were reused from verified signing cache
- **AND** current references and unrelated objects SHALL remain untouched

#### Scenario: Reference returns or cleanup is interrupted
- **WHEN** a retired object becomes referenced again
- **THEN** its retirement mark SHALL be cleared
- **AND** state-write, listing, or per-object deletion failures SHALL NOT be reported as successful cleanup

#### Scenario: Revived historical artifact is briefly advertised
- **WHEN** an old content-addressed object is advertised again and publication subsequently rolls back
- **THEN** its previous retirement timestamp SHALL NOT permit early deletion
- **AND** a fresh grace period SHALL begin when it is next observed unreferenced

### Requirement: Complete artifact content identity

Immutable IPA URLs SHALL include the complete signed artifact SHA-256, and verification of remote content SHALL use bounded memory.

#### Scenario: Re-sign the same release
- **WHEN** signing produces different bytes for the same application version
- **THEN** the new artifact SHALL have a distinct immutable URL
- **AND** retries of identical bytes SHALL reuse the same identity

## MODIFIED Requirements

### Requirement: Compensating cleanup for failed publication

The publication transaction SHALL safely remove newly uploaded unreferenced immutable objects, retaining potentially advertised uploads through the retirement grace period.

#### Scenario: Batch upload or registry promotion fails
- **WHEN** new IPA or icon objects were uploaded but the batch registry was not successfully promoted and revalidated
- **THEN** the previous registry SHALL remain or be restored
- **AND** immediate compensation SHALL delete only unreferenced keys uploaded before any registry write was attempted
- **AND** potentially advertised keys SHALL instead remain through the retirement grace period
- **AND** cleanup failures SHALL report remaining keys without masking the original failure

#### Scenario: Registry promotion or revalidation fails
- **WHEN** a registry write was attempted and publication fails
- **THEN** the previous registry SHALL be restored when possible and its cache expiry attempted
- **AND** potentially advertised objects SHALL remain available until normal retirement cleanup
- **AND** rollback failure SHALL preserve all potentially referenced artifacts and be reported explicitly
