# SideloadedIPA

SideloadedIPA is an automated pipeline that downloads, signs, verifies, and distributes iOS IPAs. It handles complex apps with nested app extensions and frameworks, automatically synchronizes provisioning profiles via the App Store Connect API, and publishes verified builds to Cloudflare R2 with an Over-The-Air (OTA) web install portal.

## Features

- **Multi-bundle Signing**: Signs main apps and nested extensions (e.g., LiveContainer, LiveProcess, Share/Widget extensions) with dedicated provisioning profiles and entitlement policies.
- **Source Tracking**: Supports direct HTTPS downloads with SHA-256 pinning as well as automatic tracking of GitHub releases.
- **Apple Developer Integration**: Automatically creates App IDs, enables required capabilities, and generates/refreshes iOS development profiles.
- **Independent Verification**: Reopens and inspects signed IPAs to verify Mach-O signatures, embedded profiles, and XML/DER entitlement consistency before publishing.
- **OTA Distribution**: Uploads signed IPAs and extracted icons to Cloudflare R2, updates `apps.json`, and serves an on-demand Next.js install page (`web/`).
- **Smart Caching**: Uses content-addressed fingerprints to skip re-signing when sources, profiles, and policies are unchanged.

## Quick Start

### 1. Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js 20+ (for the web app in `web/`)

### 2. Setup

```bash
uv sync --frozen
cp configs/tasks.toml.example configs/tasks.local.toml
```

Configure your apps in `configs/tasks.local.toml` (see the [Configuration Guide](docs/configuration.md)). Copy `.env.example` to `.env` if you need local environment credentials.

### 3. Running the Pipeline

You can run each stage individually:

```bash
run_id="local-$(date +%Y%m%d%H%M%S)"

# 1. Inspect source IPA and bundle hierarchy (read-only)
uv run sideloadedipa inspect --config configs/tasks.local.toml --run-id "$run_id" --task MyApp

# 2. Plan required Apple Developer resources (read-only)
uv run sideloadedipa plan --config configs/tasks.local.toml --run-id "$run_id" --task MyApp

# 3. Sync App IDs, capabilities, and provisioning profiles
uv run sideloadedipa sync --config configs/tasks.local.toml --run-id "$run_id" --task MyApp --apply

# 4. Sign all bundles in the IPA
uv run sideloadedipa sign --config configs/tasks.local.toml --run-id "$run_id" --task MyApp

# 5. Verify the signed IPA
uv run sideloadedipa verify --config configs/tasks.local.toml --run-id "$run_id" --task MyApp
```

Or run all local stages in one step:

```bash
uv run sideloadedipa run --config configs/tasks.local.toml --run-id "$run_id" --task MyApp --apply
```

> To publish to Cloudflare R2, add `--publish` to `verify` / `publish` and ensure R2 and Vercel credentials are configured (see the [Operator Runbook](docs/operator-runbook.md)).

## Validation & Testing

Run Python tests, linting, and type checking:

```bash
uv run --frozen pytest
uv run --frozen black --check src tests scripts
uv run --frozen isort --check-only src tests scripts
uv run --frozen mypy src/sideloadedipa scripts
```

Test and build the Next.js web application:

```bash
cd web
npm ci
npm test
APPS_DATA_MODE=fixture npm run build
```

## Documentation

- [Configuration Guide](docs/configuration.md) — Task definition, source options, multi-bundle signing, and environment variables.
- [Architecture Overview](docs/architecture.md) — Pipeline stages, evidence chain, caching model, and web distribution.
- [Operator Runbook](docs/operator-runbook.md) — Step-by-step instructions for running, debugging, qualifying backends, and handling rollbacks.
- [Security Model](docs/security.md) — Credential scoping, sandbox boundaries, and dependency integrity.
- [Troubleshooting](docs/troubleshooting.md) — Solutions for common bundle, profile, entitlement, and signing errors.
- [Migration Guide](MIGRATION.md) — Instructions for upgrading configs and command invocations.

