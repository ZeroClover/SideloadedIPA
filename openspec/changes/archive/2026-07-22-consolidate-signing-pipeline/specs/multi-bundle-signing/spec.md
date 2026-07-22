## MODIFIED Requirements

### Requirement: Qualified per-bundle signing backend

The system SHALL use a version-verified backend that can apply the plan's distinct profile and entitlement policy to every profile-bearing bundle.

#### Scenario: Use a qualified zsign backend

- **WHEN** the pinned zsign version has passed the multi-bundle backend contract fixture
- **THEN** the adapter SHALL pass all planned profiles using repeated `-m` arguments
- **AND** SHALL pass each profile-bearing bundle's planned entitlement document using the paired repeated `-e` extension selected by ADR 0001
- **AND** SHALL reject profile/entitlement count mismatches before signing
- **AND** SHALL prove the profile selected for each bundle from post-sign evidence

#### Scenario: Backend only supports one global entitlement document

- **WHEN** planned bundles require different entitlement documents and the backend cannot apply them independently
- **THEN** backend qualification SHALL fail before production signing
- **AND** the system SHALL require a per-bundle-capable backend or backend extension
- **AND** SHALL NOT substitute profile-only or global entitlements without passing the expected contract

#### Scenario: Backend version is unexpected

- **WHEN** the executable version or checksum does not match the configured supported release
- **THEN** signing SHALL fail before the certificate or source IPA is used

#### Scenario: Backend contract is exercised by automated tests

- **WHEN** pull-request CI runs the Python test suite with a built patched backend available
- **THEN** at least one automated test SHALL execute the real pinned patched backend binary against a deterministic multi-bundle fixture
- **AND** SHALL prove per-bundle profile and entitlement selection from post-sign evidence
- **AND** fake-backend argument conventions SHALL NOT be the only executable proof of the backend CLI contract
