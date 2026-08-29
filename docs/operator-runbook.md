# Operator Runbook

This guide covers common operational tasks for running, verifying, debugging, and maintaining the SideloadedIPA pipeline.

---

## 1. Local Environment Setup

Install project dependencies using the pinned uv lockfile:

```bash
uv sync --frozen
```

Run test suites and linters:

```bash
# Run pytest with 95% coverage threshold
uv run pytest

# Generate HTML coverage report (optional)
uv run pytest --cov-report=term-missing --cov-report=html

# Check formatting and typing
uv run black --check scripts/ src/sideloadedipa/
uv run isort --check-only scripts/ src/sideloadedipa/
uv run mypy src/sideloadedipa scripts/
```

Test the web frontend locally:

```bash
cd web
npm ci
npm test
APPS_DATA_MODE=fixture npm run build
```

---

## 2. Running Pipeline Stages Locally

Each run uses a unique `--run-id` to track stage manifests under `work/pipeline/<run-id>/`.

```bash
run_id="local-$(date +%Y%m%d%H%M%S)"
task="LiveContainer"
config="configs/tasks.local.toml"
```

### Stage 1: Inspect Source IPA (Read-Only)
Downloads the IPA, verifies archive integrity, and maps the bundle structure:

```bash
uv run sideloadedipa inspect --config "$config" --run-id "$run_id" --task "$task" --json
```

### Stage 2: Plan Apple Resources (Read-Only)
Calculates required App IDs, capabilities, App Groups, and provisioning profiles without making any changes in Apple Developer Portal:

```bash
uv run sideloadedipa plan --config "$config" --run-id "$run_id" --task "$task" --json
```

### Stage 3: Sync Apple Resources (Apply)
Creates missing App IDs, enables capabilities, and downloads provisioning profiles:

```bash
uv run sideloadedipa sync --config "$config" --run-id "$run_id" --task "$task" --apply --json
```

### Stage 4: Sign IPAs
Generates custom entitlements and signs all nested bundles and binaries:

```bash
uv run sideloadedipa sign --config "$config" --run-id "$run_id" --task "$task" --json
```

### Stage 5: Verify Signatures
Reopens the signed IPA independently to check code signatures, entitlements, and profiles:

```bash
uv run sideloadedipa verify --config "$config" --run-id "$run_id" --task "$task" --json
```

### Stage 6: Publish to Cloudflare R2
Uploads IPAs and icons to R2, updates `apps.json`, and triggers web cache revalidation:

```bash
uv run sideloadedipa publish --config "$config" --run-id "$run_id" --task "$task" --json
```

---

## 3. GitHub Actions Execution

The main pipeline workflow is defined in `.github/workflows/sign-and-upload.yml`.

### Automated Schedule
- Runs daily at 02:00 UTC.
- Automatically checks for new GitHub releases or changed sources and rebuilds affected tasks.

### Manual Dispatch
Trigger a manual run using the GitHub CLI:

```bash
# Normal run (uses signing cache)
gh workflow run sign-and-upload.yml

# Force full rebuild of all tasks
gh workflow run sign-and-upload.yml -f force_rebuild=true

# Enable SSH debugging on failure
gh workflow run sign-and-upload.yml -f debug=true
```

### SSH Debugging
When `debug=true` is enabled:
1. The runner launches a Dropbear SSH server on localhost.
2. A temporary Cloudflare Tunnel is established.
3. The workflow logs display connection instructions (e.g. `ssh -o ProxyCommand='cloudflared access ssh --hostname %h' ...`).
4. To finish the debug session and allow cleanup, stop cloudflared from the SSH session.

---

## 4. Backend Requalification

Whenever `zsign` source, patches, compiler toolchains, or bundle entitlement logic are modified, requalify the signing backend:

```bash
uv run sideloadedipa-qualify-backend \
  --run-id "backend-$(date +%Y%m%d%H%M%S)" \
  --evidence work/qualification/backend-qualification.json
```

On macOS with an active Apple Development certificate in Keychain, pass `--codesign-identity` and `--codesign-keychain` to compare output against Apple's native `codesign` tool.

---

## 5. Rollback & Failure Recovery

- **Atomic Publishing**: If signing, verification, or R2 uploads fail, the existing `site/apps.json` registry is left untouched. Users will continue seeing the previous stable version.
- **Rollback Procedure**: To roll back a published app:
  1. Revert the task configuration in `configs/tasks.toml`.
  2. Run the workflow with `force_rebuild=true` to rebuild and re-publish the previous version.
- **Apple Developer Resources**: `sync --apply` is additive only. It never deletes App IDs or revokes certificates. If an App ID was created with wrong capabilities, update its configuration in `tasks.toml` and re-run `sync --apply`.

---

## 6. Device Acceptance Checklist

After publishing a new build of complex apps like LiveContainer, verify the following on a physical iOS device:

1. **Installation**: Download and install via the OTA web portal.
2. **Launch & Extensions**: Verify that the main app, Launch extension, and Share extension open without crashes.
3. **App Groups**: Verify shared storage between the main app and extensions.
4. **Keychain Access**: Confirm keychain sharing works across all configured groups.
5. **Special Capabilities**: Confirm HealthKit or Increased Memory Limit work as expected if enabled.

