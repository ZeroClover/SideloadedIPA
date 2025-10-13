# IPA Signing via GitHub Actions

This repository contains a GitHub Actions workflow and helper scripts to:

- Import Apple Developer signing certificates into a keychain using `apple-actions/import-codesign-certs`.
- Automatically sync Development provisioning profiles with all registered devices via App Store Connect API.
- Read a TOML config of signing tasks.
- For each task: download the IPA, re-sign with Fastlane `resign` using synced profiles, and upload to an Assets server via `scp`.

## File Structure

- `.github/workflows/sign-and-upload.yml` — the workflow (manual and webhook triggers)
- `scripts/sync_profiles.rb` — syncs provisioning profiles with all devices via App Store Connect API
- `scripts/run_signing.py` — processes `configs/tasks.toml`, re-signs, uploads
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

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My App"
bundle_id = "com.example.myapp"
ipa_url = "https://example.com/path/to/MyApp.ipa"
asset_server_path = "/var/www/assets/ipas/"
```

**Required fields**:
- `task_name` — Identifier for this task (used for profile lookup)
- `app_name` — Human-friendly name (used for profile naming: "{app_name} Dev")
- `bundle_id` — iOS Bundle Identifier (must exist in Apple Developer Portal)
- `ipa_url` — Direct download URL of the IPA
- `asset_server_path` — Destination path on asset server (if ends with `/`, filename is appended)

See `configs/tasks.toml.example` for more details.

## Triggers

- Manual: Workflow Dispatch inputs:
  - `debug` — set to true to enable Cloudflare Tunnel for SSH debugging (default false). Debug is only supported for manual runs.
- Webhook: `repository_dispatch` with type `sign_ipas` (no debug support via webhook).

Example `repository_dispatch` payload:

```json
{
  "event_type": "sign_ipas",
  "client_payload": {}
}
```

## How It Works

1. **Import Certificates**: Uses `apple-actions/import-codesign-certs` to import P12 into temporary keychain
2. **Sync Profiles**: Ruby script (`sync_profiles.rb`) via Spaceship:
   - Fetches all Development certificates
   - Fetches all enabled iOS and macOS devices
   - Creates/updates provisioning profiles for each app
   - Downloads profiles to `work/profiles/`
3. **Sign IPAs**: Python script (`run_signing.py`):
   - Downloads IPA from specified URL
   - Re-signs with Fastlane using synced profile
   - Uploads to asset server via `scp`

## Requirements and Notes

- **Runner**: `macos-latest`
- **Tools installed**: `fastlane`, `sshpass`, `curl` (Homebrew), Ruby gems (`spaceship`, `toml-rb`)
- **Signing**: Uses Fastlane `resign` action with discovered identity and synced profile
- **Upload**: Password-based `scp` to asset server
- **Bundle IDs**: Must be pre-registered in Apple Developer Portal

### Debug Mode (Cloudflare Tunnel)

If `debug` is enabled for a manual run (workflow_dispatch), the workflow will:

- Enable macOS SSH service on port 22.
- Disable password authentication and enable public key authentication.
- Write the provided `DEBUG_SSH_PUBLIC_KEY` to `~runner/.ssh/authorized_keys`.
- Install `cloudflared` and run `cloudflared --no-autoupdate --url ssh://localhost:22` in the foreground.

Use the private key that corresponds to `DEBUG_SSH_PUBLIC_KEY` to connect to the printed trycloudflare.com hostname. The tunnel runs in the foreground and keeps the job alive until you exit or cancel the run.

## Latest Actions Versions

- `actions/checkout@v5`
- `actions/setup-python@v5`
- `apple-actions/import-codesign-certs@v2`

These are selected based on current docs and should be kept up to date.
