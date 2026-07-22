## Why

The archived multi-bundle signing change implemented the required domain services but left several of them disconnected from the production CLI and GitHub Actions workflow. As a result, production does not enforce manifest ordering, complete cache fingerprints, cache-hit revalidation, aggregated inventory preflight, complete run reporting, or cancellation evidence even though the corresponding tasks were marked complete.

## What Changes

- Replace the production `GITHUB_OUTPUT`/legacy change-selection path with one package-owned, manifest-driven orchestration entry point.
- Complete the standalone `verify` use case and make every production stage consume durable typed predecessor evidence.
- Inventory and reconcile every selected source before Apple mutation, reporting all knowable configuration and inventory-policy errors together.
- Use complete per-task fingerprints for selective rebuilds and fully reopen/revalidate cached artifacts before reuse.
- Emit one redacted complete run report and cancellation evidence for real production runs.
- Record non-secret per-node signing backend evidence and bound successful subprocess output.
- Remove orphaned uploads on failed publication transactions without touching previously referenced objects.
- Restrict production secrets to the steps that need them and ensure SSH debug sessions do not inherit signing or publication credentials.
- Replace placeholder purposes in the six signing specifications with maintained descriptions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `signing-workflow-orchestration`: Require the production CLI and workflow, rather than fixture-only paths, to use stage manifests, complete cache decisions/revalidation, reports, cancellation evidence, and least-privilege secret scoping.
- `signing-task-configuration`: Require aggregated current-source inventory preflight before the production Apple apply boundary.
- `multi-bundle-signing`: Require populated per-node backend evidence and bounded captured output on successful backend execution.

## Impact

The production CLI composition, package orchestration, cache storage, workflow step boundaries, signing adapter evidence, publication gateway, tests, operator documentation, and CI artifact format are affected. Existing task configuration and public registry schema remain compatible; cache and report schemas will be versioned where their persisted representation changes.
