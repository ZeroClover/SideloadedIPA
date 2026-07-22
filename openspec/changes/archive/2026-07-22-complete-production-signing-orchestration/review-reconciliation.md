# Review reconciliation

This corrective change resolves the findings raised after
`add-multi-bundle-ipa-signing` was archived.

| Finding | Resolution and evidence | Residual risk |
| --- | --- | --- |
| Production bypassed the manifest/cache/report orchestration | All default CLI commands now use `production_pipeline.py`; the production workflow invokes inspect, plan, sync, sign, verify, and publish as visible stages over one run-isolated manifest chain. The two credentialed CI runs below exercise the real Apple, zsign, verification, cache, R2, and revalidation composition. | None. |
| Standalone `verify` was a placeholder | `verify` reconstructs the signing plan from current signed inputs, checks retained plan/artifact evidence, independently reopens every IPA, and writes the VERIFY manifest/report. | None. |
| Per-node backend evidence was empty | The zsign adapter reopens its output, reinventories the graph, and records executable, embedded-profile, and signed-entitlement digests for every planned node; graph mismatch fails closed. Production retains the canonical signing report and digest with the cached artifact, and validates both before report reuse. | The evidence is intentionally non-secret and does not replace independent signature verification. |
| Legacy removal preceded final production parity | The new engine is the only production path and the workflow no longer consumes legacy selection output. Historical ordering cannot be changed retroactively; rollback is the last verified registry/configuration rather than an in-run legacy engine switch. | A rollback requires redeploying the last verified revision. |
| Aggregated preflight had no production caller | `inspect` inventories current selected assets and aggregates policy diagnostics across tasks before Apple apply; failure-injection tests prove no Apple call occurs. | Apple state can still change after the read-only plan and is rechecked during apply/signing. |
| Publication could leave orphan uploads | Batch failure compensates newly uploaded, previously unreferenced IPA and icon keys after upload, registry, or revalidation failure while restoring the prior registry. | Cleanup failure is reported with the exact unreferenced keys for operator action. |
| Signing spec Purpose sections were placeholders | All six signing-related main specs now state their production purpose. | None. |
| SSH debug inherited production secrets | Production credentials are scoped to minimum steps; the shared debug action unsets Apple, GitHub, R2, revalidation, and webhook credentials from all long-lived SSH/tunnel/wait processes. | A debug session still exposes non-secret workspace files and retained redacted evidence by design. |
| Successful stdout was unbounded | Successful and failed subprocess output is redacted and bounded. ASC receives a separate 16 MiB success bound so large structured lists remain parseable, while failures retain the 64 KiB evidence bound. | The bounds are configurable for controlled diagnostics. |

The OpenSSL CMS `-noverify` behavior remains intentional: the pipeline pins the
planned certificate/profile hashes and does not use the Linux host CA store as
an Apple trust oracle. Device installation/launch and the independent macOS
oracle remain the platform trust checks.

## Credentialed production evidence

- Run [29888067131](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29888067131)
  completed all visible production stages for all six tasks on commit `5b801ca`.
  Cache schema v2 forced six `first-run` rebuilds; every task passed standalone
  verification and safe publication. The retained signing reports covered 139
  planned bundle nodes, all with non-empty backend evidence.
- Run [29888458069](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29888458069)
  repeated the same commit and selected `cache-hit` with `rebuild=false` for all
  six tasks. Every signing-report digest exactly matched the rebuild run, all
  cached IPAs passed full reopen verification, and publication completed.

Historical task-order evidence cannot be recreated after removal of the legacy
engine. Per the accepted time-blocked policy, the last verified registry and
revision remain the explicit rollback boundary; this observation-only item is
complete with that residual risk recorded.
