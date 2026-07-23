# signed-ipa-verification Specification

## Purpose
Define independent post-signing verification that reopens an IPA and proves its bundle graph, profiles, entitlements, signatures, and package integrity before reuse or publication.
## Requirements
### Requirement: Three-way entitlement verification

The system MUST compare the policy-generated expected entitlements, provisioning-profile authorization, and signed executable entitlements for every profile-bearing bundle.

#### Scenario: Expected entitlement is present and authorized

- **WHEN** a required key/value is authorized by the mapped profile and present with the expected value in the signed executable
- **THEN** that entitlement check SHALL pass

#### Scenario: Profile does not authorize an expected value

- **WHEN** a required expected value is absent from or outside the profile's authorization
- **THEN** verification SHALL fail for that bundle even if the signing tool returned success

#### Scenario: Signed executable loses a required value

- **WHEN** a profile authorizes an expected value but the signed executable omits or changes it
- **THEN** verification SHALL fail with expected, authorized, and actual redacted values

#### Scenario: Signed executable gains an undeclared entitlement

- **WHEN** the signed executable contains a non-default entitlement not declared or allowed by policy
- **THEN** verification SHALL fail as unplanned entitlement drift

### Requirement: Semantic entitlement comparison

The system SHALL compare entitlements by typed semantics rather than plist bytes and SHALL use narrowly defined authorization rules.

#### Scenario: Compare unordered entitlement arrays

- **WHEN** an entitlement's documented semantics are an unordered set
- **THEN** ordering differences SHALL NOT fail verification
- **AND** missing, extra, or changed values SHALL remain detectable

#### Scenario: Validate team-bound identifiers

- **WHEN** application identifiers, team identifiers, or keychain groups contain a team/App Identifier Prefix
- **THEN** the signed values SHALL use the planned team prefix and target bundle policy
- **AND** source placeholder or upstream-team prefixes SHALL be rejected

#### Scenario: Profile uses an allowed wildcard

- **WHEN** a documented profile entitlement wildcard authorizes an exact expected value
- **THEN** profile authorization MAY pass for that value
- **AND** the signed executable SHALL still contain the exact expected value rather than the wildcard

#### Scenario: XML and DER entitlements disagree

- **WHEN** a signed executable contains both representations with different semantic content
- **THEN** verification SHALL fail

### Requirement: Embedded profile and bundle identity verification

The system SHALL reopen the output IPA and prove that every profile-bearing bundle has the exact planned `CFBundleIdentifier`, embedded profile, team, certificate authorization, and validity.

#### Scenario: Verify per-bundle profile mapping

- **WHEN** an output contains multiple profile-bearing bundles
- **THEN** each embedded profile's application identifier SHALL map to that bundle's target identifier
- **AND** no root profile SHALL be accepted for a differently identified extension

#### Scenario: Embedded profile is missing, expired, or wrong

- **WHEN** any profile-bearing bundle lacks its planned profile or contains a mismatched/expired profile
- **THEN** the entire IPA SHALL fail verification

### Requirement: Complete nested-signature verification

The system SHALL cryptographically inspect every executable node from the planned graph after signing and SHALL validate the nested sealing relationship.

#### Scenario: All nested code is valid

- **WHEN** every planned framework, dylib, extension, nested app, and root executable has a valid signature from the intended identity and parent seals are valid
- **THEN** the signature gate SHALL pass

#### Scenario: One nested signature is stale or invalid

- **WHEN** any discovered nested executable retains an invalid or unintended signature
- **THEN** verification SHALL fail with its graph path
- **AND** root-signature success SHALL NOT mask the failure

### Requirement: Output package integrity and graph parity

The system SHALL validate the repackaged IPA structure against the signing plan and SHALL reject unplanned executable or profile-bearing content.

#### Scenario: Repackaged graph matches plan

- **WHEN** output inventory contains the planned nodes, identifiers, parent edges, and non-signing payload content
- **THEN** package-integrity verification SHALL pass

#### Scenario: Node is missing or added after planning

- **WHEN** the output omits a planned node or includes an unplanned profile-bearing/executable node
- **THEN** verification SHALL fail and require a new inventory and plan

#### Scenario: IPA cannot be reopened safely

- **WHEN** the output archive is malformed or violates safe-extraction constraints
- **THEN** it SHALL fail before publication

### Requirement: Configured functional-entitlement contract

The system SHALL verify every profile-bearing bundle against the exact functional-entitlement contract declared by its task policy, including configured membership and cardinality constraints, without embedding application- or release-specific constants in the verifier.

#### Scenario: Verify an exact configured collection

- **WHEN** a bundle policy declares the exact allowed values or expected count for a set-like entitlement
- **THEN** the signed bundle SHALL contain exactly the declared values with the planned team-bound transformations
- **AND** any missing, extra, duplicated, or wrongly prefixed value SHALL fail verification

#### Scenario: Verify different contracts for cooperating bundles

- **WHEN** a root app and its extensions declare different entitlement contracts
- **THEN** each bundle SHALL be checked against its own target identifier, profile, and expected entitlement document
- **AND** no bundle SHALL inherit another bundle's entitlements unless its policy explicitly declares them

#### Scenario: Verify a configured bundle-graph variant

- **WHEN** a task selects a release variant with an additional profile-bearing bundle
- **THEN** verification SHALL require an independently planned identifier, profile, and reviewed entitlement contract for that bundle
- **AND** an undeclared additional bundle or copied root policy SHALL fail verification

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

### Requirement: Single authoritative verification pass per run

The system SHALL execute the complete independent verifier exactly once per output artifact per production run, in the verification stage, and SHALL make every other consuming stage validate that pass's canonical evidence by digest instead of re-executing the verifier.

#### Scenario: Verification stage runs the complete pass

- **WHEN** the verification stage executes for a freshly signed or cache-reused artifact
- **THEN** the complete verifier SHALL reopen the artifact and produce the canonical verification result
- **AND** the verification report digest and artifact digest SHALL be recorded in the run's stage evidence

#### Scenario: Signing stage exposes output without a complete pass

- **WHEN** the signing backend completes for a task
- **THEN** the signing stage SHALL validate backend result evidence, including plan identity and per-node and output digests, before exposing the artifact
- **AND** the complete verifier SHALL NOT execute within the signing stage
- **AND** cache promotion and publication SHALL remain gated on the run's verification pass

#### Scenario: Publication consumes verification evidence

- **WHEN** publication evaluates an artifact for upload and registry promotion
- **THEN** it SHALL validate the verification-stage manifest, verification report digest, and artifact digest for the same run and task
- **AND** SHALL NOT re-execute the complete verifier

#### Scenario: Verification evidence is missing or mismatched

- **WHEN** a consuming stage cannot validate the pass's evidence because the manifest is absent, the digests differ, or the evidence belongs to another run or task
- **THEN** that stage and every downstream side effect SHALL fail closed

### Requirement: Single-extraction verification pass

One verification pass SHALL derive all required findings from one safe extraction of the output artifact, and one safe extraction of the source artifact where a check requires it, shared across its checks.

#### Scenario: Checks share one extraction

- **WHEN** the complete verifier executes
- **THEN** the output artifact SHALL be safely extracted once per pass
- **AND** entitlement, profile, signature, and integrity checks SHALL consume the same extracted tree
- **AND** whole-artifact digests SHALL be computed once per artifact per pass

#### Scenario: Safe extraction fails

- **WHEN** the artifact cannot be safely extracted
- **THEN** the pass SHALL fail with no required check marked passed
- **AND** cache promotion and publication SHALL be blocked
