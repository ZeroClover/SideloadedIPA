## ADDED Requirements

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
