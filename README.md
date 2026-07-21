# IPA Signing via GitHub Actions

This repository contains a GitHub Actions workflow and helper scripts to:

- Automatically sync Development provisioning profiles with all registered devices via App Store Connect CLI (`asc`).
- Read a TOML config of signing tasks.
- For each task: download the IPA (from direct URL or GitHub Release), re-sign with [`zsign`](https://github.com/zhlynn/zsign) (directly from the P12 certificate, no Keychain) using synced profiles, and upload to Cloudflare R2 (S3-compatible, via `boto3`) under a versioned key.
- **Publish the registry**: merge each result into `site/apps.json` on R2 — the single data source for the download page and the itms.plist manifests, both served by the Vercel-hosted front-end.
- **Refresh the edge cache**: call the front-end's on-demand revalidation hook, then delete stale versioned IPA keys no longer referenced by the registry.
- **Intelligent caching**: Only rebuild IPAs when releases are updated or devices change, reducing workflow runtime and costs.

## File Structure

- `.github/workflows/sign-and-upload.yml` — the workflow (manual, webhook, and scheduled triggers)
- `scripts/sync_profiles_asc.py` — syncs provisioning profiles with all devices via App Store Connect CLI
- `scripts/run_signing.py` — processes `configs/tasks.toml`, re-signs, uploads to R2, publishes the registry
- `scripts/r2_store.py` — Cloudflare R2 storage wrapper (boto3): uploads, apps.json IO, stale-key cleanup
- `scripts/apps_registry.py` — merges signing results into the `site/apps.json` registry on R2
- `scripts/check_changes.py` — detects changes to determine which tasks need rebuilding
- `configs/tasks.toml` — TOML config defining signing tasks (and the optional `[r2]` object-layout settings)
- `configs/tasks.toml.example` — example configuration file
- `.env.example` — example environment variables
- `web/` — the Vercel-hosted download page (Next.js): renders the app grid and the dynamic `itms.plist` route from apps.json

## Required Secrets / Variables

Set these at Repository → Settings → Secrets and variables → Actions:

### Apple Developer Credentials

- `APPLE_DEV_CERT_P12_ENCODED` — Base64-encoded Apple Developer signing P12 certificate
- `APPLE_DEV_CERT_PASSWORD` — Password for the P12 certificate

### App Store Connect API (for automatic profile sync)

- `ASC_KEY_ID` — App Store Connect API Key ID (e.g., `ABC123XYZ`)
- `ASC_ISSUER_ID` — App Store Connect API Issuer ID (UUID format)
- `ASC_PRIVATE_KEY` — Base64-encoded `.p8` private key content

> Generate API keys at: https://appstoreconnect.apple.com/access/api (requires "Developer" role)

### Cloudflare R2 (publishing target)

- `R2_ACCOUNT_ID` — Cloudflare account ID (builds the S3 endpoint host)
- `R2_ACCESS_KEY_ID` — R2 API token access key (Object Read & Write, scoped to the bucket)
- `R2_SECRET_ACCESS_KEY` — R2 API token secret
- `R2_BUCKET` — bucket name
- `R2_PUBLIC_BASE_URL` — public base URL of the bucket's custom domain (e.g., `https://ipa.zeroclover.io`)

### Vercel (download page)

- `VERCEL_REVALIDATE_SECRET` — shared secret for the front-end's `/api/revalidate` hook

### Optional

- `R2_REGION` — S3 signing region: the bucket's location hint (`wnam`/`enam`/`weur`/`eeur`/`apac`/`oc`) or `auto` (default)
- `DEBUG_SSH_PUBLIC_KEY` — SSH public key for debug mode (only required when `debug=true`)

## Provisioning Profile Management

The workflow automatically creates/updates Development provisioning profiles via App Store Connect CLI (`asc`), including:
- All enabled iOS devices (iPhone and iPad classes)
- All available Development certificates

Provisioning profiles are downloaded to `work/profiles/` and used directly for signing. If profile sync fails, the entire workflow will fail.

## TOML Config

Edit `configs/tasks.toml` and add entries like:

### Option 1: Direct IPA URL (existing behavior)

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My App"
bundle_id = "com.example.myapp"
ipa_url = "https://example.com/path/to/MyApp.ipa"
```

### Option 2: GitHub Release Tracking (new, with caching)

```toml
[[tasks]]
task_name = "AnotherApp"
app_name = "Another App"
bundle_id = "com.example.anotherapp"
repo_url = "https://github.com/owner/repo"
release_glob = "*.ipa"          # Optional, default: "*.ipa"
use_prerelease = false           # Optional, default: false
slug = "anotherapp"              # Optional, default: slugified app_name
icon_path = "ios/Runner/Assets.xcassets/AppIcon.appiconset/Icon-App-1024x1024@1x.png"
```

**Required fields**:
- `task_name` — Identifier for this task (used for profile lookup)
- `app_name` — Human-friendly name (used for profile naming: "{app_name} Dev")
- `bundle_id` — iOS Bundle Identifier (must exist in Apple Developer Portal)
- **Either** `ipa_url` OR `repo_url` (mutually exclusive)
  - `ipa_url` — Direct download URL of the IPA (always rebuilds)
  - `repo_url` — GitHub repository URL (enables version tracking and caching)

**Optional fields**:
- `slug` — Stable key for R2 object paths and page/plist URLs (default: slugified `app_name`)
- `release_glob` — Pattern to match release assets (default: `*.ipa`)
- `use_prerelease` — Whether to use prerelease versions (default: `false`)
  - If `true`, fetches latest prerelease; falls back to latest stable if none exist
  - If `false`, fetches only latest stable release
- `icon_path` — Where to get the app's card icon. Nothing is inferred, because
  upstream repository layouts differ too much; each task names its own source:
  - `"<path>"` — a path inside `repo_url`, fetched at the release tag so the icon
    matches the published build
  - `"https://…"` — any absolute URL
  - `"ipa:"` — the signed IPA itself, for projects whose repository has no square
    master (Icon Composer ships SVG layers). Caps out at 152×152, so prefer a
    repository asset when one exists.

  Any raster format is accepted — detected by magic bytes, not file extension,
  since upstream does commit WebP data named `.png` — and normalised to a square
  PNG of at most 512×512. Point it at a **square, full-bleed** master: the
  download page rounds corners with a CSS mask, so a pre-rounded source ends up
  visibly double-rounded. Omit the field to leave the existing icon untouched —
  the task then reports no icon and the registry keeps the URL it already has.

See `configs/tasks.toml.example` for more details.

## Triggers

- **Scheduled**: Daily at 02:00 UTC (keeps cache fresh and auto-processes new releases)
- **Manual**: Workflow Dispatch inputs:
  - `debug` — Enable Cloudflare Tunnel for SSH debugging (default: `false`)
  - `force_rebuild` — Force full rebuild ignoring cache (default: `false`)
- **Webhook**: `repository_dispatch` with type `sign_ipas`

Example `repository_dispatch` payload:

```json
{
  "event_type": "sign_ipas",
  "client_payload": {}
}
```

## How It Works

1. **Restore Cache**: Restores cached device lists and release versions from previous runs
2. **Install zsign**: Downloads [`zsign`](https://github.com/zhlynn/zsign)'s official prebuilt Linux binary (pinned via `ZSIGN_VERSION`, checksum-verified). The static `musl` build has no runtime dependencies, and `zsign` signs straight from the P12 — no Keychain involved
3. **Check Entitlements Profile**: Python script (`sync_profiles_asc.py check`) via App Store Connect CLI:
   - Fetches all enabled iOS devices
   - Saves device list snapshot to cache for change detection
   - Compares with cached device list to detect changes
   - Verifies `tasks.toml` apps have corresponding provisioning profiles
4. **Check App Version**: Python script (`check_changes.py`):
   - Uses device-change status + `force_rebuild` to decide whether to rebuild all
   - Checks GitHub release versions vs cache to decide which tasks need rebuilding
5. **Sync Entitlements Profile**: Python script (`sync_profiles_asc.py`) via App Store Connect CLI:
   - If device list changed → regenerates all provisioning profiles and downloads them
   - If device list unchanged → downloads existing profiles and creates missing ones if needed
6. **Sign IPAs**: Python script (`run_signing.py`):
   - For `ipa_url` tasks: Always downloads and rebuilds
   - For `repo_url` tasks:
     - Fetches latest release via authenticated GitHub API
     - Compares version with cache
     - Only rebuilds if version or publish timestamp changed
   - Re-signs with `zsign` using the P12 certificate and synced profile
   - Reads the signed IPA's actual bundle id + version, uploads the IPA to R2 under a versioned, immutable key (`apps/<slug>/<version>/<App>.ipa`)
   - Uploads the card icon under a content-addressed, immutable key (`apps/<slug>/icon-<sha12>.png`), so a changed icon lands on a fresh URL rather than waiting out the zone's 4-hour browser cache
   - Updates release cache with new versions
7. **Publish registry**: merges results into `site/apps.json` on R2, calls the Vercel `/api/revalidate` hook (shared secret), then deletes stale keys — superseded IPA versions and superseded icons alike — that the registry no longer references (skipped if any step fails)
8. **Save Cache**: Saves updated cache state for next run

## Caching Behavior

The workflow uses GitHub Actions cache to minimize unnecessary work:

- **Cache Storage**: `work/cache/` directory containing:
  - `device-list.json` — Snapshot of registered devices with checksum
  - `release-versions.json` — Tracked release versions and timestamps

- **Cache Lifetime**: 7 days of inactivity (refreshed by daily scheduled runs)

- **Change Detection Logic**:
  - Device list changes → Full rebuild (all profiles regenerated, all IPAs re-signed)
  - Release version changes → Rebuild only affected IPA
  - Direct URL (`ipa_url`) tasks → Always rebuild (no version tracking)
  - New tasks or first run → Always rebuild

- **Force Rebuild**: Use the `force_rebuild` input to bypass cache and rebuild everything

## Requirements and Notes

- **Runner**: `ubuntu-latest` — `zsign` signs via OpenSSL (not Apple's `codesign`/Security.framework), so the whole pipeline runs on Linux (≈10× cheaper than a macOS runner)
- **Tools installed**: `zsign` (prebuilt static Linux binary), `asc` (App Store Connect CLI, prebuilt Linux binary) — all checksum-verified where downloaded; `boto3` comes from `uv.lock`
- **Signing**: Uses [`zsign`](https://github.com/zhlynn/zsign) with the P12 certificate and synced profile (no Keychain / codesign identity required)
- **Publishing**: S3-compatible uploads to Cloudflare R2 (zero egress fees); the download page and `itms.plist` manifests are served by Vercel — no self-hosted server anywhere in the pipeline
- **Bundle IDs**: Must be pre-registered in Apple Developer Portal
- **GitHub Token**: Workflow automatically uses `GITHUB_TOKEN` for authenticated API access
  - Provides 1,000 requests/hour per repository (vs 60/hour unauthenticated)
  - Avoids shared runner IP rate limiting
  - No additional configuration required (default `contents: read` permission)

### Debug Mode (Cloudflare Tunnel)

If `debug` is enabled for a manual run (workflow_dispatch), the workflow will:

- Write the provided `DEBUG_SSH_PUBLIC_KEY` to `~runner/.ssh/authorized_keys`.
- Start a throwaway [`dropbear`](https://matt.ucc.asn.au/dropbear/dropbear.html) SSH server on `127.0.0.1:2222` — public-key auth only (password auth disabled), with a per-run host key.
- Download `cloudflared` and run `cloudflared --no-autoupdate --url ssh://localhost:2222` in the foreground, which prints a `trycloudflare.com` hostname.

Connect with the private key matching `DEBUG_SSH_PUBLIC_KEY`, tunnelling raw TCP through Cloudflare (end-to-end encrypted, no third-party SSH relay):

```bash
ssh -o ProxyCommand='cloudflared access tcp --hostname <printed-host>.trycloudflare.com' runner@localhost
```

The tunnel runs in the foreground and keeps the job alive until you exit or cancel the run.

## Local Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for Python dependency management.

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed

### Setup

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Sync dependencies**:
   ```bash
   uv sync
   ```

   This will:
   - Create a virtual environment at `.venv/`
   - Install all project dependencies
   - Install development dependencies (pytest, mypy, black, isort)

3. **Run scripts**:
   ```bash
   # Run check_changes.py
   uv run python scripts/check_changes.py

   # Run run_signing.py
   uv run python scripts/run_signing.py
   ```

4. **Run tests** (when available):
   ```bash
   uv run pytest
   ```

5. **Format code**:
   ```bash
   # Format with black
   uv run black scripts/

   # Sort imports with isort
   uv run isort scripts/

   # Type check with mypy
   uv run mypy scripts/
   ```

### Why uv?

- **Fast**: 10-100x faster than pip
- **Reliable**: Lockfile ensures reproducible installs
- **Simple**: Single tool for virtual environments and dependencies
- **Compatible**: Works with standard `pyproject.toml`

## Latest Actions Versions

- `actions/checkout@v6`
- `astral-sh/setup-uv@v8`
- `actions/cache@v5`

These are selected based on current docs and should be kept up to date.
