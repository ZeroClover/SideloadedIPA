# Production rollout comparison

Status: complete. Two consecutive production `workflow_dispatch` runs on the
same commit exercise the rebuild and cache-hit paths. The event type does not
change the production job, credentials, cache, verification, or publication
code paths.

## Runs

### Successful pre-rollout baseline

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29976733318
- Event/time: `workflow_dispatch`, 2026-07-23 03:20:14 UTC
- Commit: `09c5cf8c429dcaa7d5fa60783c47bc8fb821947f`
- Artifact: `signing-run-report-29976733318`
- Scope: 7 tasks, 12 profile-bearing bundles, 50 operations
  (`38 no-op`, `12 safe-automatic`)
- Apple stages: standalone plan took 40 seconds; standalone apply took
  472 seconds; total 512 seconds
- Apple mutations: `created_apple_resources` was empty
- Verification: all tasks passed; finding counts in retained task order were
  `202, 185, 22, 133, 69, 90, 17`
- Publication: all 7 tasks recorded a completed publish stage and result

### Post-rollout forced-rebuild path

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29990631705
- Event/time: `workflow_dispatch`, 2026-07-23 08:15:03 UTC
- Commit: `98ce92c3f473e99b1063af5445696087c8b389ec`
- Inputs: `debug=false`, `force_rebuild=true`
- Artifact: `signing-run-report-29990631705`
- Cache decision: all 7 tasks recorded `reason=forced`, `rebuild=true`
- ASC adapter invocations: 38
- Combined Apple plan/apply duration: 16 seconds
- Verification: exactly 7 task `07-verify` manifests and 7 canonical
  verification reports; all passed, with finding counts
  `202, 185, 22, 133, 69, 90, 17`
- Publication: all 7 tasks completed all 9 stages and recorded a publication
  result; the consolidated report recorded `passed=true`

### Post-rollout cache-hit path

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29990838209
- Event/time: `workflow_dispatch`, 2026-07-23 08:19:15 UTC
- Commit: `98ce92c3f473e99b1063af5445696087c8b389ec`
- Inputs: `debug=false`, `force_rebuild=false`
- Artifact: `signing-run-report-29990838209`
- Cache decision: all 7 tasks recorded `reason=cache-hit`, `rebuild=false`, and
  a non-null cached artifact identity
- ASC adapter invocations: 38
- Combined Apple plan/apply duration: 11 seconds
- Verification: exactly 7 task `07-verify` manifests and 7 canonical
  verification reports; every cached artifact was reopened and all passed
- Publication: all 7 tasks completed all 9 stages and recorded a publication
  result; the consolidated report recorded `passed=true`

## ASC invocation reduction

The pre-rollout artifact predates the redacted integer counter, so the baseline
uses a conservative lower bound from the exact pre-rollout code and that run's
artifact:

- The old workflow created separate plan and apply processes, and apply ended
  with a third complete collection.
- One old complete collection issued `4 + N + 4M` JSON commands, where `N` is
  all App IDs and `M` is all development profiles.
- The artifact proves 12 existing target App IDs, 12 profile-bearing bundles,
  and no Apple resource creation, so conservatively `N >= 12` and `M >= 12`.
- Every old bundle ensure listed App IDs once. Every old profile ensure
  re-enumerated all profiles (`1 + 4M`) and downloaded at least one matching
  profile (`+1`). Capability ensure calls are deliberately omitted from the
  lower bound.
- The two CLI processes each also issued one version command.

The conservative minimum is therefore:

```text
2 + 3 * (4 + 12 + 4 * 12) + 12 + 12 * (2 + 4 * 12) = 806
```

Both post-rollout paths measured 38 adapter invocations. The reduction is at
least `1 - 38 / 806 = 95.29%`, exceeding the 85% target. Apple-stage wall time
also fell from 512 seconds to 16 seconds on forced rebuild and 11 seconds on
cache hit.

## Behavior-equivalence and verification evidence

- In each post-rollout run, `02-apple-plan.json` was byte-equivalent after
  canonical JSON rendering to
  `03-apple-apply.json.resource_plan`.
- The forced-rebuild and cache-hit plan documents were identical to each other,
  with snapshot
  `2de3b0ba5e883c476812f9f843783f5d44bd8a559dea35042c5595ed788ee663`.
- Per-task artifact digests, verification plan digests, pass/fail values, and
  complete finding documents were identical between the forced-rebuild and
  cache-hit runs.
- Both paths produced exactly one canonical verify manifest/report per task.
  The sign and publish stages consumed digest-bound evidence; the cache-hit path
  did not bypass the verify stage.
- Existing failure-injection acceptance tests prove missing/mismatched
  verification evidence and tampered artifacts still block publication. The
  production paths retained the same all-pass findings and publication outcome.

## Acceptance matrix

| Evidence | Forced rebuild | Cache hit |
| --- | --- | --- |
| Run | `29990631705` | `29990838209` |
| Commit | `98ce92c3f473e99b1063af5445696087c8b389ec` | same |
| Cache decision | 7/7 `forced` | 7/7 `cache-hit` |
| ASC invocations | 38 | 38 |
| Conservative reduction | at least 95.29% | at least 95.29% |
| Canonical verify manifests/reports | 7/7 | 7/7 |
| Plan/apply equality | equal | equal |
| Findings vs paired run | identical | identical |
| Publication | 7/7 succeeded | 7/7 succeeded |
| Fail-closed gate | retained | retained and not bypassed |
