# IPA Signing via GitHub Actions

This repository contains a GitHub Actions workflow and helper scripts to:

- Import Apple Developer signing certificates into a keychain using `apple-actions/import-codesign-certs`.
- Automatically sync Development provisioning profiles with all registered devices via App Store Connect API.
- Read a TOML config of signing tasks.
- For each task: download the IPA (from direct URL or GitHub Release), re-sign with Fastlane `resign` using synced profiles, and upload to an Assets server via `scp`.
- **Intelligent caching**: Only rebuild IPAs when releases are updated or devices change, reducing workflow runtime and costs.

## File Structure

- `.github/workflows/sign-and-upload.yml` — the workflow (manual, webhook, and scheduled triggers)
- `scripts/sync_profiles.rb` — syncs provisioning profiles with all devices via App Store Connect API
- `scripts/run_signing.py` — processes `configs/tasks.toml`, re-signs, uploads (with GitHub API integration)
- `scripts/check_changes.py` — detects changes to determine which tasks need rebuilding
- `configs/tasks.toml` — TOML config defining signing tasks
- `configs/tasks.toml.example` — example configuration file
- `.env.example` — example environment variables
- `Gemfile` — Ruby dependencies (spaceship, toml-rb)

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

### Asset Server

- `ASSETS_SERVER_IP` — SSH server IP or hostname
- `ASSETS_SERVER_USER` — SSH username
- `ASSETS_SERVER_CREDENTIALS` — SSH password

### Optional

- `DEBUG_SSH_PUBLIC_KEY` — SSH public key for debug mode (only required when `debug=true`)

## Provisioning Profile Management

The workflow automatically creates/updates Development provisioning profiles via App Store Connect API, including:
- All registered iOS and macOS devices
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
asset_server_path = "/var/www/assets/ipas/"
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
asset_server_path = "/var/www/assets/"
```

**Required fields**:
- `task_name` — Identifier for this task (used for profile lookup)
- `app_name` — Human-friendly name (used for profile naming: "{app_name} Dev")
- `bundle_id` — iOS Bundle Identifier (must exist in Apple Developer Portal)
- **Either** `ipa_url` OR `repo_url` (mutually exclusive)
  - `ipa_url` — Direct download URL of the IPA (always rebuilds)
  - `repo_url` — GitHub repository URL (enables version tracking and caching)
- `asset_server_path` — Destination path on asset server (if ends with `/`, filename is appended)

**Optional fields for GitHub Release tracking**:
- `release_glob` — Pattern to match release assets (default: `*.ipa`)
- `use_prerelease` — Whether to use prerelease versions (default: `false`)
  - If `true`, fetches latest prerelease; falls back to latest stable if none exist
  - If `false`, fetches only latest stable release

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
2. **Import Certificates**: Uses `apple-actions/import-codesign-certs` to import P12 into temporary keychain
3. **Check Entitlements Profile**: Ruby script (`sync_profiles.rb check`) via Spaceship:
   - Fetches all enabled iOS devices
   - Saves device list snapshot to cache for change detection
   - Compares with cached device list to detect changes
   - Verifies `tasks.toml` apps have corresponding provisioning profiles
4. **Check App Version**: Python script (`check_changes.py`):
   - Uses device-change status + `force_rebuild` to decide whether to rebuild all
   - Checks GitHub release versions vs cache to decide which tasks need rebuilding
5. **Sync Entitlements Profile**: Ruby script (`sync_profiles.rb`) via Spaceship:
   - If device list changed → regenerates all provisioning profiles and downloads them
   - If device list unchanged → downloads existing profiles and creates missing ones if needed
6. **Sign IPAs**: Python script (`run_signing.py`):
   - For `ipa_url` tasks: Always downloads and rebuilds
   - For `repo_url` tasks:
     - Fetches latest release via authenticated GitHub API
     - Compares version with cache
     - Only rebuilds if version or publish timestamp changed
   - Re-signs with Fastlane using synced profile
   - Uploads to asset server via `scp`
   - Updates release cache with new versions
7. **Save Cache**: Saves updated cache state for next run

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

- **Runner**: `macos-latest`
- **Tools installed**: `fastlane`, `sshpass`, `curl` (Homebrew), Ruby gems (`spaceship`, `toml-rb`)
- **Signing**: Uses Fastlane `resign` action with discovered identity and synced profile
- **Upload**: Password-based `scp` to asset server
- **Bundle IDs**: Must be pre-registered in Apple Developer Portal
- **GitHub Token**: Workflow automatically uses `GITHUB_TOKEN` for authenticated API access
  - Provides 1,000 requests/hour per repository (vs 60/hour unauthenticated)
  - Avoids shared runner IP rate limiting
  - No additional configuration required (default `contents: read` permission)

### Debug Mode (Cloudflare Tunnel)

If `debug` is enabled for a manual run (workflow_dispatch), the workflow will:

- Enable macOS SSH service on port 22.
- Disable password authentication and enable public key authentication.
- Write the provided `DEBUG_SSH_PUBLIC_KEY` to `~runner/.ssh/authorized_keys`.
- Install `cloudflared` and run `cloudflared --no-autoupdate --url ssh://localhost:22` in the foreground.

Use the private key that corresponds to `DEBUG_SSH_PUBLIC_KEY` to connect to the printed trycloudflare.com hostname. The tunnel runs in the foreground and keeps the job alive until you exit or cancel the run.

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

- `actions/checkout@v5`
- `astral-sh/setup-uv@v5`
- `apple-actions/import-codesign-certs@v2`

These are selected based on current docs and should be kept up to date.
