# IPA Signing via GitHub Actions

This repository contains a GitHub Actions workflow and helper scripts to:

- Download `zsign` from `ZSIGN_BINARY_URL` and decode an Apple Dev P12 cert.
- Read a TOML config of signing tasks.
- For each task: download the IPA, decode a mobile provisioning profile from an environment variable, sign with `zsign`, and upload to an Assets server via `scp`.

## File Structure

- `.github/workflows/sign-and-upload.yml` — the workflow (manual and webhook triggers)
- `scripts/prepare_env.sh` — downloads/unzips `zsign` and decodes `apple_dev.p12`
- `scripts/run_signing.py` — processes `configs/tasks.toml`, signs, uploads
- `configs/tasks.toml` — placeholder TOML config defining tasks
- `configs/mobileprovision/<TASK_NAME>.mobileprovision.b64` — optional fallback base64 mobile provisioning files

## Required Secrets / Variables

Set these at Repository → Settings → Secrets and variables → Actions:

- `ZSIGN_BINARY_URL` — URL to a zip containing `zsign` (saved as `zsign.zip`)
- `APPLE_DEV_CERT_P12_ENCODED` — Base64-encoded Apple Developer signing P12
- `APPLE_DEV_CERT_PASSWORD` — Password for the P12
- `ASSETS_SERVER_IP` — SSH server IP
- `ASSETS_SERVER_USER` — SSH username
- `ASSETS_SERVER_CREDENTIALS` — SSH password

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

- Manual: Workflow Dispatch with optional inputs:
  - `config_path` — path to TOML (default `configs/tasks.toml`)
  - `zsign_url` — optional override for `ZSIGN_BINARY_URL`
- Webhook: `repository_dispatch` with type `sign_ipas`. Optional `client_payload` fields supported:
  - `config_path`
  - `zsign_url`

Example `repository_dispatch` payload:

```json
{
  "event_type": "sign_ipas",
  "client_payload": {
    "config_path": "configs/tasks.toml",
    "zsign_url": "https://example.com/zsign-linux-amd64.zip"
  }
}
```

## Requirements and Notes

- Runner: `ubuntu-latest`
- Tools installed: `sshpass`, `unzip`, `curl`
- `scripts/prepare_env.sh` produces `./zsign` and `./apple_dev.p12` in the workspace root.
- The signing step runs:

```
./zsign -k apple_dev.p12 -p "$APPLE_DEV_CERT_PASSWORD" -m profile.mobileprovision -o <AppName>.ipa <AppName>_ori.ipa
```

- Upload uses password-based `scp` as requested.

## Latest Actions Versions

- `actions/checkout@v5`
- `actions/setup-python@v5`

These are selected based on current docs and should be kept up to date.
