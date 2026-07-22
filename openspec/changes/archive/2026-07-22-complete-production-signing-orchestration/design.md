## Context

The package already contains typed stage manifests, complete cache fingerprints, cache-hit revalidation, run-report serialization, cancellation journaling, independent verification, and atomic publication. Production bypasses those components: GitHub Actions selects tasks with a legacy release-version script, applies Apple changes before current-source inventory, and invokes a command that signs, verifies, and publishes without durable stage evidence.

The correction must preserve the existing task configuration, R2 registry schema, content-addressed object naming, and verified signing behavior. It must also remain usable locally and in GitHub-hosted Linux runners without adding a new service or dependency.

## Goals / Non-Goals

**Goals:**

- Make package-owned orchestration the only production selection and execution authority.
- Persist and validate per-task stage manifests between visible CI steps.
- Perform current-source inventory and aggregated policy preflight before Apple mutation.
- Select rebuilds from complete fingerprints and reverify every cache hit.
- Provide independently callable signing, verification, and publication steps plus an end-to-end `run` command.
- Produce one complete redacted report and cancellation record from production evidence.
- Limit credentials to the workflow steps that require them and keep them out of SSH debug sessions.

**Non-Goals:**

- Automating Apple capabilities that require portal or human approval.
- Changing bundle policies, App IDs, registry schema, public URLs, or physical-device acceptance rules.
- Reintroducing the removed legacy signing engine as a rollback implementation.
- Guaranteeing deletion of immutable objects that were already referenced by any registry version.

## Decisions

### Use a filesystem-backed production stage store

Each selected task stores canonical manifests under `work/pipeline/<run-id>/<task>/`. A command validates the predecessor manifest and the digest of recomputed/current inputs before performing its stage. The CI run ID is the default production run identity; local callers may provide one explicitly. This reuses the existing manifest domain model and makes step boundaries inspectable without introducing a database.

Alternative considered: keep manifests only inside one Python process. That would leave GitHub Actions step boundaries and standalone commands unverified.

### Separate stage commands while retaining `run`

`inspect`, `plan`, `sync`, `sign`, `verify`, and publication execute through one production orchestration service. `run` invokes the same operations in order. Signing retains its immediate internal verification before artifact promotion; the standalone verification step reopens the promoted artifact again and emits the durable verification-stage evidence used by publication.

Alternative considered: make signing expose an unverified promoted IPA. That would weaken the existing fail-closed promotion boundary.

### Recompute typed plans at trust boundaries

The implementation persists canonical digests and source/artifact files, not Python object graphs or unsafe serialization. Later commands reconstruct the typed graph, profiles, and signing plan from authenticated inputs and require their digests to match predecessor evidence. This costs some local parsing but avoids a second deserializer for security-sensitive plans.

### Cache only complete verified signing results

The cache index stores a complete fingerprint, artifact digest, verification-report digest, and immutable task artifact path. A hit is accepted only after current profiles/prerequisites are validated and the cached IPA passes the full independent verifier. Index promotion occurs only after the selected batch reaches its configured success boundary; GitHub cache saving remains gated on final success.

### Treat publication uploads as a compensating transaction

The gateway gains deletion for explicit newly uploaded keys. If a later upload, registry replacement, or revalidation fails, the service restores the previous registry when necessary and deletes only unreferenced objects created by that attempt. Existing/stale objects remain protected.

### Scope secrets per step and isolate debug

Tool versions and non-secret paths remain job-level variables. Apple, certificate, GitHub, R2, and revalidation secrets are attached only to the steps that need them. The SSH composite action runs without those environment variables; no command writes secret values to `GITHUB_ENV` or persistent pipeline reports. GitHub-hosted runner files that contain private certificate material remain temporary and are deleted within their owning command.

## Risks / Trade-offs

- [Recomputing inventory and plans adds CI time] → Cache the downloaded source within the run and reuse it while still rechecking canonical digests.
- [A cache index from an older schema is present] → Treat it as a full rebuild and replace it only after success.
- [Cancellation occurs during an uncertain remote operation] → Record the operation/resource identity, preserve the previous publication, and require operator inspection rather than destructive cleanup.
- [Compensating object deletion fails] → Keep the previous registry active and report the explicit orphan keys for later safe cleanup.
- [Standalone stage invoked without predecessor state] → Fail closed with a command that identifies the missing stage and run ID.

## Migration Plan

1. Add the production stage store and real command composition behind tests while retaining the current workflow.
2. Add complete cache and report integration, standalone verification/publication, and failure-injection coverage.
3. Change GitHub Actions to visible package-owned stages and remove `check_changes.py` from production selection.
4. Run PR checks and a non-publishing development-branch workflow, then a forced production batch.
5. Run a second workflow that produces at least one cache hit and confirm full revalidation evidence.
6. Roll back by redeploying the last verified registry/object set and reverting the workflow commit; do not restore a second signing engine.

## Open Questions

None. The existing batch-atomic publication policy, task configuration, and physical-device acceptance records remain authoritative.
