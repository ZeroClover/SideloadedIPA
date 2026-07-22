## Why

The manual multi-bundle canary no longer satisfies the production CLI manifest contract, and qualification debug sessions can start while decoded signing material remains on disk. Production evidence also overstates timing precision and lacks equivalent failure-injection coverage at the real orchestration boundary.

## What Changes

- Run the private LiveContainer canary through the complete non-publishing production stage chain and validate the current run-report schema.
- Destroy decoded qualification keys, certificates, profiles, and keychains before any SSH debug session starts; scope non-production credentials to the steps that use them.
- Add stage-by-stage failure injection against `ProductionPipeline`, proving upstream failures block downstream adapters and side effects.
- Record real stage intervals, represent unavailable per-node timing honestly, and retain accurate cleanup diagnostics including uploaded icon keys.
- Remove the unused legacy change selector after confirming no production or supported compatibility caller remains.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `signing-workflow-orchestration`: Tighten manual canary orchestration, debug credential cleanup, production failure isolation, timing evidence, publication cleanup diagnostics, and post-migration legacy removal requirements.

## Impact

Affected areas are the manual and qualification jobs in `.github/workflows/sign-and-upload.yml`, production stage recording and publication diagnostics, workflow and production integration tests, operator documentation, and the obsolete `check_changes` compatibility module and tests. Daily production behavior and published artifact identity remain unchanged.
