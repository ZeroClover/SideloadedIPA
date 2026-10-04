# Failure diagnosis

Read when an observed diagnostic matches a case below. Use error codes and redacted
reports, not raw credentials/profiles. Sources: src/sideloadedipa/errors.py and the
responsible module named in each case. Operations requiring live effects remain
subject to [AGENTS.md](../AGENTS.md).

| Symptom | Inspect | Safe next action |
| --- | --- | --- |
| source.asset-match-count (zero/multiple matches) | sources/github.py; release asset names and release_glob | Choose exactly one intended variant; do not select the first match |
| Direct SHA-256 mismatch | sources/download.py; configured digest, trusted upstream evidence, partial transfer | Stop intake; independently establish provenance before accepting new bytes/digest |
| Unknown profile-bearing bundle | ipa/graph.py, signing/planner.py; new source inventory | Review the added app/extension and explicit bundle policy; do not suppress discovery |
| manual-required App Group association | apple/planning.py, configs/tasks.toml | Account Holder/Admin completes portal action; record reviewed alias in manual_app_group_associations; profile must still authorize exact group |
| apple.profile-entitlement-unauthorized | signing/profile_validation.py; intended capability and profile evidence | Verify capability/manual prerequisites; authorized sync --apply can obtain a valid replacement |
| LiveContainer keychain groups lost | configs/signing/livecontainer/root-process.plist; root/LiveProcess policy | Confirm template mode and all required groups; avoid fallback to profile defaults |
| XML/DER entitlement disagreement | verification/entitlements.py, three_way.py | Block publication; qualify changed backend against macOS oracle |
| Nested signature failure | verification report's deepest failing path; signing/order.py | Check child-before-parent order, certificate/profile pairing and transformed graph |
| Catalog/plist returns 503 | web/lib/apps.ts; APPS_DATA_MODE, R2_APPS_JSON_URL, origin response | Repair dependency/configuration; do not synthesize an empty successful catalog |
| Revalidation 405 / 401 | web/lib/revalidation.ts; method and secret names | Use POST; compare configured secret presence securely, never print values |
| Retirement state error | adapters/publication/r2_store.py; redacted GC diagnostic | Stop cleanup and recover reviewed state; do not delete sidecar to force collection |

For source mismatch, preserve expected/actual hashes and lengths as evidence; a new
local hash is not proof the replacement is trusted. For functional entitlement loss,
allowed_entitlement_drops is a reviewed policy decision with rationale, not a way to
silence verification. Do not retry apply/publish blindly after ambiguous side effects.

Run focused offline regressions for the failing boundary using
[Development](development.md). Live recovery/qualification procedures are in
[Operations](operator-runbook.md) and [Publication](publication.md).
