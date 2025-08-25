# IPA Signing via GitHub Actions

This repository contains a GitHub Actions workflow and helper scripts to:

- Import Apple Developer signing certificates into a keychain using `apple-actions/import-codesign-certs`.
- Read a TOML config of signing tasks.
- For each task: download the IPA, decode a mobile provisioning profile from an environment variable, re-sign with Fastlane `resign`, and upload to an Assets server via `scp`.

## File Structure

- `.github/workflows/sign-and-upload.yml` — the workflow (manual and webhook triggers)
- `scripts/run_signing.py` — processes `configs/tasks.toml`, re-signs, uploads
- `configs/tasks.toml` — placeholder TOML config defining tasks
- `configs/mobileprovision/<TASK_NAME>.mobileprovision.b64` — optional fallback base64 mobile provisioning files

## Required Secrets / Variables

Set these at Repository → Settings → Secrets and variables → Actions:

- `APPLE_DEV_CERT_P12_ENCODED` — Base64-encoded Apple Developer signing P12
- `APPLE_DEV_CERT_PASSWORD` — Password for the P12
- `ASSETS_SERVER_IP` — SSH server IP
- `ASSETS_SERVER_USER` — SSH username
- `ASSETS_SERVER_CREDENTIALS` — SSH password

Required for debug mode when `debug=true`:

- `DEBUG_SSH_PUBLIC_KEY` — SSH public key string to authorize for the `runner` user during debug sessions.

Per-task mobile provisioning profiles (Base64) must be provided via environment variables named `<TASK_NAME>_MOBILEPROVISION`, for example:

- `ABC_MOBILEPROVISION` — Base64 content of the provisioning profile for task `ABC`
- `DEF_MOBILEPROVISION` — Base64 content for task `DEF`

Because GitHub Actions does not auto-expose arbitrary secrets as environment variables, you have two options:

1) Explicitly map them in the workflow (recommended):

```yaml
jobs:
  sign-and-upload:
    env:
      ABC_MOBILEPROVISION: ${{ secrets.ABC_MOBILEPROVISION }}
      DEF_MOBILEPROVISION: ${{ secrets.DEF_MOBILEPROVISION }}
```

2) Place base64 files at `configs/mobileprovision/<TASK_NAME>.mobileprovision.b64` as a fallback (the script will use them only if the env var is not set).

## TOML Config

Edit `configs/tasks.toml` and add entries like:

```toml
[[tasks]]
task_name = "ABC"
app_name = "ExampleApp"
ipa_url = "https://example.com/path/to/ExampleApp.ipa"
asset_server_path = "/var/www/assets/ipas/"
```

- `asset_server_path`: If it ends with `/`, the signed file name is appended. Otherwise, it is treated as a full destination file path.

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

## Requirements and Notes

- Runner: `macos-latest`
- Tools installed: `fastlane`, `sshpass`, `curl` (via Homebrew)
- Apple signing certificates are imported via `apple-actions/import-codesign-certs` into a temporary keychain.
- The signing step uses Fastlane `resign`, e.g.:

```
fastlane run resign ipa:"path/to/app.ipa" signing_identity:"Apple Distribution: Your Name (TEAMID)" provisioning_profile:"path/to/profile.mobileprovision" keychain_path:"$KEYCHAIN_PATH"
```

- Upload uses password-based `scp` as requested.

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
