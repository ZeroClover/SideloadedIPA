## ADDED Requirements

### Requirement: Single-sourced signing primitives
Well-known entitlement key names, canonical JSON serialization with digesting, and immutable-JSON thawing used by signing policy, profile validation, cache fingerprinting, and entitlement verification SHALL each have exactly one shared definition that consumers import rather than re-declare.

#### Scenario: Entitlement key referenced by multiple layers
- **WHEN** domain entitlement policy, provisioning-profile validation, signing planning, or entitlement comparison references a well-known entitlement key such as `application-identifier`
- **THEN** it SHALL import the shared domain-owned constant for that key
- **AND** no production module SHALL re-declare a private copy of the same key literal

#### Scenario: JSON document digested for a durable contract
- **WHEN** any pipeline, cache, manifest, or verification component computes a SHA-256 over a JSON document
- **THEN** it SHALL use the shared canonical serialization and digest helper
- **AND** digest output for existing documents SHALL remain byte-identical across the refactor, proven by golden-value tests

#### Scenario: Frozen JSON values returned to mutable form
- **WHEN** a component converts immutable domain JSON value pairs back into a dictionary
- **THEN** it SHALL use the shared thaw helper
- **AND** no module SHALL carry a private copy of the thaw comprehension

### Requirement: Cohesive stage module ownership
Production signing and publication stage logic SHALL live in the `pipeline/stages/` package without parallel flat stage modules, and the stage import graph SHALL remain acyclic under the architecture-guard tests.

#### Scenario: Signing stage support code is needed
- **WHEN** fingerprint construction or cached-report restoration logic is required by the signing stage
- **THEN** it SHALL reside in `pipeline/stages/` alongside its only consumer
- **AND** no `pipeline/sign_stage.py` or `pipeline/publish_stage.py` flat module SHALL exist

#### Scenario: Stage modules import each other
- **WHEN** the architecture-guard test analyzes `pipeline/stages/`
- **THEN** the import graph SHALL be acyclic
- **AND** no stage module SHALL import `pipeline.production` or use bucket imports from `sideloadedipa.domain`
