## Context

The repository currently has three workflows and four composite actions. Production publication occupies only the first quarter of `sign-and-upload.yml`; the remainder contains migration-era shadow, Apple-state, qualification, and canary paths selected by seven independent booleans. PR validation duplicates checkout and environment setup across four jobs, and its Ruby check only confirms that an action metadata document has a composite `runs.steps` shape. Registered-device and production publication acceptance are already complete, while the production pipeline itself independently verifies every artifact before atomic publication.

## Goals / Non-Goals

**Goals:**

- Make the production workflow represent one operation: verified production publication.
- Retain manual SSH access to the actual CI environment after a failed or successful run.
- Make PR CI one coherent, reproducible validation environment with stronger workflow and composite-action analysis.
- Remove CI-only migration surfaces and their unused composite action.
- Preserve the daily schedule, manual force rebuild, repository dispatch compatibility, cache behavior, evidence retention, and publication safety gates.

**Non-Goals:**

- Remove the local inspect, plan, Apple-state, qualification, or checksum-pinned integration tools and tests.
- Change signing, verification, R2 registry, revalidation, or cache semantics.
- Remove SSH debug or weaken its credential isolation.
- Change the existing `repository_dispatch` contract.

## Decisions

1. **Production has one execution mode.** `sign-and-upload.yml` retains `debug` and `force_rebuild`; every scheduled, webhook, or manual run executes the production job. The dispatch guard and all conditional auxiliary jobs are deleted because their owning inputs no longer exist. Keeping a choice-valued mode input was rejected: the removed modes are not routine production operations and should not remain advertised by the production workflow.

2. **Verified publication replaces private canary publication testing.** A newly configured task may be explicitly enabled for publication and exercised through the same inspect, plan/apply, sign, independent verification, atomic registry, and rollback path used in production. A separate non-publishing path was rejected because it does not test R2, registry, revalidation, or public ITMS installation and a new task has no prior registry entry to displace.

3. **PR validation uses one job and therefore one debuggable environment.** Python tests, formatting, typing, tool installation, workflow analysis, web tests, and web build run sequentially in one job. The SSH step uses `always()` and follows every validation step, so a manual debug dispatch enters the environment that actually performed all checks. Multiple parallel jobs were rejected because they duplicate setup and create ambiguous multiple SSH sessions.

4. **Use complementary Actions validators.** `actionlint` remains responsible for Workflow syntax, expressions, and embedded shell checks. Pinned `zizmor` 1.28.0 runs with strict collection over the repository, covering both workflows and composite action metadata plus security findings. The Ruby structure probe is removed because it neither validates GitHub's action schema nor analyzes action shell/security behavior. A new Python YAML dependency was rejected because it would only reproduce the same shallow shape check.

5. **Pin every external Action by commit digest.** Human-readable version comments remain beside immutable digests. This lets the blocking `zizmor` high-severity gate pass without suppressing its unpinned-use policy and prevents a mutable tag from changing CI execution.

6. **Delete only CI consumers, not diagnostic tooling.** The standalone integration workflow and qualification-fixture composite action are removed. Opt-in real-IPA tests and package-owned qualification utilities remain locally callable, avoiding an unrelated application-code deletion.

## Risks / Trade-offs

- [One PR job reduces parallelism and can increase wall-clock time] → uv, npm, and patched-zsign caches remain enabled; the simpler environment gives deterministic failure ordering and one useful SSH session.
- [Direct publication exposes a newly added app immediately] → publication remains explicitly enabled per task, signing output passes independent verification, registry promotion is atomic, and a new task has no old entry to overwrite.
- [Removing scheduled real-IPA integration reduces recurring upstream monitoring] → source selection and inventory still execute daily in production, while the checksum-pinned integration test remains available for deliberate local execution.
- [A new zizmor release could change findings] → version 1.28.0 is locked in `uv.lock`; upgrades are reviewed dependency changes.
- [Immutable Action pins are less visually obvious] → each pin retains a version comment and static tests enforce full-length digests.

## Migration Plan

1. Add the OpenSpec delta and update static workflow tests for the intended two-input production surface.
2. Collapse PR validation and add pinned zizmor plus immutable Action references.
3. Delete the integration workflow, auxiliary production jobs, and unused qualification-fixture action.
4. Synchronize README, runbook, and security documentation.
5. Run pytest with coverage, strict mypy, formatting, actionlint, zizmor, web tests/build, OpenSpec strict validation, and diff checks.

Rollback is a normal Git revert; no Apple, R2, registry, or cache data migration is involved.

## Open Questions

None.
