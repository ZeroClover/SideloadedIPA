# SideloadedIPA

SideloadedIPA is a fail-closed pipeline for selecting, inspecting, provisioning,
signing, verifying, and publishing iOS IPA releases. It handles nested apps and
extensions as one bundle graph, assigns a distinct profile and entitlement policy
to every profile-bearing bundle, and publishes only independently verified output.

The repository also contains the Next.js download application in `web/`. The
application reads one validated `apps.json` registry from Cloudflare R2 and serves
the corresponding OTA installation manifests.

## Safety properties

- GitHub release selection must resolve to exactly one asset. Direct IPA URLs must
  be HTTPS and pinned to a reviewed SHA-256 digest.
- Downloads are bounded, streamed, digest checked, and rejected before inventory
  or side effects when their identity changes.
- Every stage consumes a canonical predecessor manifest bound to the run, task,
  source, graph, and file digest. Signed output is still reopened and inspected
  independently.
- Unknown profile-bearing bundles, missing bundle rules, entitlement/profile
  mismatches, invalid signatures, and tampered cache entries stop publication.
- Apple changes are additive and explicitly gated. Publication is ordered as
  verified output, immutable upload, atomic registry update, revalidation, then
  stale-object cleanup.

## Quick start

Use the repository-pinned Python version and the exact uv version required by
`pyproject.toml`:

```bash
uv sync --frozen
cp configs/tasks.toml.example configs/tasks.local.toml
```

Edit the local task file using [the configuration reference](docs/configuration.md).
Export credentials only for stages that require them; `.env.example` lists the
supported names. Never commit credential values.

Run a task through the visible production stages with one unique run ID:

```bash
run_id="local-$(date +%Y%m%d%H%M%S)"

uv run sideloadedipa inspect --config configs/tasks.local.toml \
  --run-id "$run_id" --task MyApp
uv run sideloadedipa plan --config configs/tasks.local.toml \
  --run-id "$run_id" --task MyApp
uv run sideloadedipa sync --config configs/tasks.local.toml \
  --run-id "$run_id" --task MyApp --apply
uv run sideloadedipa sign --config configs/tasks.local.toml \
  --run-id "$run_id" --task MyApp
uv run sideloadedipa verify --config configs/tasks.local.toml \
  --run-id "$run_id" --task MyApp
```

`inspect` is read-only. Review `plan` before adding `--apply` to `sync`.
Publication additionally requires `publication_enabled = true`, a verification
run with `--publish`, and the R2/revalidation environment described in the
[operator runbook](docs/operator-runbook.md). `sideloadedipa run --apply` is the
non-publishing convenience composition; add `--publish` only for an intentional
production publication.

Use `--json` for canonical machine-readable output and `--help` on the root or any
subcommand for the supported CLI contract.

## Validation

Run the locked Python checks from the repository root:

```bash
uv run --frozen pytest
uv run --frozen black --check src tests scripts
uv run --frozen isort --check-only src tests scripts
uv run --frozen mypy src/sideloadedipa scripts
```

HTML coverage is an opt-in diagnostic:

```bash
uv run --frozen pytest --cov-report=term-missing --cov-report=html
```

Validate the download application using its explicit fixture mode:

```bash
cd web
npm ci
npm test
APPS_DATA_MODE=fixture npm run build
```

Production web deployments use `APPS_DATA_MODE=origin`, an HTTPS
`R2_APPS_JSON_URL`, `REVALIDATE_SECRET`, and `SITE_PUBLIC_BASE_URL`. Fixture mode
is rejected when `VERCEL_ENV=production`.

## Documentation

- [Configuration](docs/configuration.md) — task, source, signing, publication,
  environment, and web settings.
- [Architecture](docs/architecture.md) — stage ownership, evidence chain, trust
  boundaries, cache, publication, and registry behavior.
- [Operator runbook](docs/operator-runbook.md) — planning, applying, publishing,
  backend qualification, retry, rollback, and device acceptance.
- [Security](docs/security.md) — archive, credential, CI, dependency, and Apple
  mutation controls.
- [Troubleshooting](docs/troubleshooting.md) — typed failure diagnosis for bundle,
  entitlement, profile, and signature problems.
- [Migration](MIGRATION.md) — only the currently supported configuration and CLI
  migrations.

The supported repository-local OpenSpec instructions live only in `.codex/skills`.
Historical implementation plans belong in Git history and archived OpenSpec
changes, not in the operational documentation set.
