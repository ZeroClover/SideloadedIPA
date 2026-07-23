# Production rollout comparison

Status: awaiting rollout and post-rollout scheduled runs. This file records the
available pre-rollout evidence without claiming tasks 5.3 or 5.4 complete.

## Required scheduled baseline

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29976134119
- Event/time: `schedule`, 2026-07-23 03:06:38 UTC
- Commit: `13c24882f7c895ac45e32fb28101bdde87e89ce2`
- Outcome: failed in the standalone verify step; publication was skipped
- Uploaded artifact: `signing-run-report-29976134119`
- Plan/apply scope: 7 tasks, 12 profile-bearing bundles, 50 operations
  (`38 no-op`, `12 safe-automatic`)
- Plan/apply equivalence: equal snapshot
  `ca736447a68894df6e10791d4ff253873a0c774bba474fdd317ed00fa7bfc012`
  and identical normalized task/operation documents after removing only
  command/apply/status and apply-only manifest fields
- Cache path: all 7 tasks were cache misses (`fingerprint-changed`)
- Limitation: the failed verify step left `05-verify.json` empty, so this run
  cannot establish verification findings, verifier execution count, or a
  successful publication baseline
- Limitation: the workflow report and job log do not emit an `asc` subprocess
  counter, so an observed invocation count cannot be recovered from this
  artifact

## Supplementary successful pre-rollout run

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29976733318
- Event/time: `workflow_dispatch`, 2026-07-23 03:20:14 UTC
- Commit: `09c5cf8c429dcaa7d5fa60783c47bc8fb821947f`
- Outcome: all 7 tasks verified and published successfully
- Uploaded artifact: `signing-run-report-29976733318`
- Plan/apply scope and snapshot: identical to the scheduled baseline above
- Cache path: all 7 tasks were cache misses (`fingerprint-changed`)
- Verification: every task recorded `passed=true`; finding counts by retained
  task order were `202, 185, 22, 133, 69, 90, 17`
- Publication: all 7 tasks contain a completed publish stage and publication
  result
- Limitation: this is a manual production run, not the scheduled run required
  by task 5.3, and the retained report records the verification result rather
  than the number of verifier executions

## Supplementary successful post-rollout debug run

- Run: https://github.com/ZeroClover/SideloadedIPA/actions/runs/29988342462
- Event/time: `workflow_dispatch`, 2026-07-23 07:28:43 UTC
- Commit: `7111977683f5508d778e4afb7d245291a6c418f8`
- Outcome: the combined Apple plan/apply, sign, single verify stage, and publish
  steps all succeeded for 7 tasks
- Uploaded artifact: `signing-run-report-29988342462`
- Plan/apply scope: 7 tasks, 50 operations (`38 no-op`, `12 safe-automatic`);
  the standalone plan artifact and the apply report's embedded resource plan
  were identical after removing command/apply/status fields, with snapshot
  `2de3b0ba5e883c476812f9f843783f5d44bd8a559dea35042c5595ed788ee663`
- Cache path: all 7 tasks were cache misses (`fingerprint-changed`)
- Verification: every task recorded `passed=true`; finding counts by retained
  task order were `202, 185, 22, 133, 69, 90, 17`
- Publication: every task completed all 9 stages and recorded a publication
  result; the consolidated report recorded `passed=true`
- Authenticated SSH follow-up: the production credential environment was
  retained for debug, and the live ASC profile list/view contract was verified
  as recorded in `asc-3.1.1-profile-contract-probe.md`
- Limitation: this is a manual cache-miss run, not either scheduled post-rollout
  row required by tasks 5.3/5.4; the report still has no ASC invocation counter
  or explicit verifier-execution counter

## Post-rollout collection checklist

Do not mark tasks 5.3 or 5.4 complete until all rows below are populated from
real scheduled-run artifacts produced by the rolled-out implementation.

| Evidence | Cache miss scheduled run | Cache hit scheduled run |
| --- | --- | --- |
| Run URL and commit | pending | pending |
| `asc` invocation count | pending | pending |
| Reduction from measured baseline | pending | pending |
| Verifier executions per task | pending; expected `1` | pending; expected `1` |
| `02-apple-plan.json` equals `03-apple-apply.json.resource_plan` | pending | pending |
| Verification findings unchanged | pending | pending |
| Publication outcome | pending | pending |
| Tamper/failure remains fail-closed | pending | pending |

The post-rollout evidence also needs an observable invocation counter; report a
wrapper/runner count in the scheduled job or retain equivalent redacted
telemetry before evaluating the 85% reduction.
