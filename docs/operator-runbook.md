# Pipeline operations

Read when running stages, diagnosing CI, or qualifying the backend. For local tests
use [Development](development.md). Load [Environment](environment.md) when supplying
credentials and [Publication](publication.md) before any publishing/recovery work.
Commands below are reference procedures, not standing execution authorization.

## Stage contract

Source: src/sideloadedipa/cli.py and pipeline/production.py. All commands accept
--config, repeatable --task, --run-id, and --json. Omitting --task can select multiple
tasks; explicitly select the intended task for local work. Use a unique run ID and
the same config/task/run ID across stages; retain manifests in work/pipeline/<run-id>/.

| Command | Effect / additional flags |
| --- | --- |
| inspect | Download and inventory; local writes, no Apple mutation |
| plan | Read Apple state and certificate identity, emit resource plan |
| sync | Plan unless --apply authorizes additive resource changes |
| sign | Sign or reuse cache; --force-rebuild bypasses signing reuse |
| verify | Independent verification; --publish defers cache promotion to publication, does not itself upload |
| publish | Publish from valid evidence; no --publish flag exists on this command |
| run | Inspect and plan by default; --apply adds sync/sign/verify, --publish adds publication, --force-rebuild skips signing reuse |

### Inspection and plan

```bash
run_id="local-$(date +%Y%m%d%H%M%S)"
task="LiveContainer"
config="configs/tasks.local.toml"
uv run --frozen sideloadedipa inspect --config "$config" --run-id "$run_id" --task "$task" --json
uv run --frozen sideloadedipa plan --config "$config" --run-id "$run_id" --task "$task" --json
```

plan and sync without --apply are non-mutating but may need Apple/P12 credentials.
A dry sync records resource-plan evidence only, not resource-apply success. Check
resource intents; resolve manual-required or blocked prerequisites before applying.

### Authorized non-publishing signing

```bash
uv run --frozen sideloadedipa sync --config "$config" --run-id "$run_id" --task "$task" --apply --json
uv run --frozen sideloadedipa sign --config "$config" --run-id "$run_id" --task "$task" --json
uv run --frozen sideloadedipa verify --config "$config" --run-id "$run_id" --task "$task" --json
```

For one process, use run with --apply instead of the three separate commands;
prepared inputs are reused within that transaction. Adding --publish is a distinct
publication action, not part of routine verification.

### Authorized publication

After device acceptance and publication_enabled=true, verify before publishing.
For a split-stage transaction, --publish defers cache promotion until publication:

```bash
uv run --frozen sideloadedipa verify --config "$config" --run-id "$run_id" --task "$task" --publish --json
uv run --frozen sideloadedipa publish --config "$config" --run-id "$run_id" --task "$task" --json
```

Alternatively run --apply --publish performs the full transaction. Keep only one
publisher active. Read [Publication](publication.md) for recovery and retention.

## GitHub Actions

.github/workflows/sign-and-upload.yml runs daily at 02:00 UTC and supports manual
workflow_dispatch. Inspect its current inputs and publication gates before dispatch:

```bash
gh workflow run sign-and-upload.yml
gh workflow run sign-and-upload.yml -f force_rebuild=true
gh workflow run sign-and-upload.yml -f debug=true
```

These commands invoke production operations. debug=true starts the single SSH
helper after the pipeline steps, including on failure. Follow actual runner output
for connection details; stop the tunnel to release the session. Do not infer that
force_rebuild selects an older release or repairs invalid configuration.

## Backend qualification

Trigger: changing zsign source/patch, compiler identity, profile pairing, or bundle
entitlement logic. Source: src/sideloadedipa/tools/qualify_backend.py and
patches/zsign/qualification-contract.json. This tool can access real Apple state
and signing material; it is not an ordinary unit test.

```bash
uv run --frozen sideloadedipa-qualify-backend   --config "$config" --task "$task" --run-id "backend-$(date +%Y%m%d%H%M%S)"   --evidence work/qualification/backend-qualification.json
```

Provide --zsign/--zsign-sha256 or ZSIGN_BIN/ZSIGN_SHA256. --apply permits Apple
synchronization and requires separate authorization. On macOS, --codesign-identity
and --codesign-keychain select the oracle; --oracle-summary supplies existing oracle
evidence. Match contract identity and inputs rather than trusting an old passing JSON.

## Device acceptance

Real-device acceptance checks installation, root app launch, required extensions,
shared App Group storage, configured keychain groups, and special capabilities such
as HealthKit/Increased Memory Limit. Record the source/build identity and actual
results before enabling publication. Offline fixture passes do not satisfy this gate.
