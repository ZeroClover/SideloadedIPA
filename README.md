# IPA Signing via GitHub Actions

This repository contains a typed Python package and GitHub Actions workflows to:

- Automatically sync Development provisioning profiles with all registered devices via App Store Connect CLI (`asc`).
- Read a TOML config of signing tasks.
- For each task: download the IPA (from direct URL or GitHub Release), re-sign with [`zsign`](https://github.com/zhlynn/zsign) (directly from the P12 certificate, no Keychain) using synced profiles, and upload to Cloudflare R2 (S3-compatible, via `boto3`) under a versioned key.
- **Publish the registry**: merge each result into `site/apps.json` on R2 — the single data source for the download page and the itms.plist manifests, both served by the Vercel-hosted front-end.
- **Refresh the edge cache**: call the front-end's on-demand revalidation hook, then delete stale versioned IPA keys no longer referenced by the registry.
- **Verified caching**: Rebuild only affected IPAs, while reopening and independently verifying every matching cached artifact before reuse.
- **Multi-bundle safety**: Inventory, plan, sign, and independently verify one
  profile per app/extension with task-specific identifiers and entitlements.

## File Structure

- `.github/workflows/sign-and-upload.yml` — production, shadow, state-probe, and private qualification jobs
- `.github/workflows/pr-checks.yml` — formatting, typing, coverage, real patched-zsign, workflow, and web checks
- `.github/workflows/integration.yml` — scheduled checksum-pinned LiveContainer inventory checks
- `.github/actions/` — reusable checksum-verified `asc`, patched-zsign, qualification-fixture, and SSH actions
- `src/sideloadedipa/apple/` — Apple command backend, expected-entitlement planning, reporting, and command sequencing
- `src/sideloadedipa/signing/` — signing plans, profile validation/storage, execution, and reports
- `src/sideloadedipa/cache/` — complete fingerprints, rebuild decisions, reuse validation, and storage
- `src/sideloadedipa/pipeline/` — manifest-driven production stages, source state, cache orchestration, verification, and publication
- `src/sideloadedipa/adapters/publication/` — Cloudflare R2 and icon adapters
- `src/sideloadedipa/tools/` — package-native qualification CLIs invoked with `python -m`
- `configs/tasks.toml` — TOML config defining signing tasks (and the optional `[r2]` object-layout settings)
- `configs/tasks.toml.example` — example configuration file
- `configs/signing/` — reviewed entitlement plist templates with typed placeholders
- `docs/operator-runbook.md` — inspect, Apple reconciliation, canary, rollback, and device acceptance
- `docs/security.md` — archive, credential, CI, dependency, and rotation controls
- `docs/troubleshooting.md` — fail-closed diagnostics for source, profile, entitlement, and signature failures
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
  - The effective pattern must match exactly one asset. Zero or multiple matches
    fail and list the candidates; use an exact name when a release has variants.
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

### Multi-bundle tasks

Multi-bundle tasks declare one exact rule for every profile-bearing source
Bundle ID:

```toml
[[tasks]]
task_name = "LiveContainer"
app_name = "LiveContainer"
bundle_id = "io.zeroclover.app.livecontainer"
repo_url = "https://github.com/LiveContainer/LiveContainer"
release_glob = "LiveContainer.ipa"
slug = "LiveContainer"
icon_path = "Resources/Assets.xcassets/AppIcon.appiconset/AppIcon1024.png"
publication_enabled = false

[tasks.signing]
id_strategy = "preserve-source-suffix"
unknown_profile_bundles = "error"
profile_type = "IOS_APP_DEVELOPMENT"
manual_app_group_associations = ["shared"]

[tasks.signing.app_groups]
shared = "group.io.zeroclover.app.livecontainer"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer"
target_bundle_id = "io.zeroclover.app.livecontainer"
role = "root"
required_capabilities = ["APP_GROUPS", "HEALTHKIT", "INCREASED_MEMORY_LIMIT", "KEYCHAIN_SHARING", "CLINICAL_HEALTH_RECORDS", "HEALTHKIT_BACKGROUND_DELIVERY"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/root-process.plist"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.LiveProcess"
required_capabilities = ["APP_GROUPS", "HEALTHKIT", "INCREASED_MEMORY_LIMIT", "KEYCHAIN_SHARING", "CLINICAL_HEALTH_RECORDS", "HEALTHKIT_BACKGROUND_DELIVERY"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/root-process.plist"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.LaunchAppExtension"
required_capabilities = ["APP_GROUPS"]
entitlement_mode = "profile"

[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.ShareExtension"
required_capabilities = ["APP_GROUPS"]
entitlement_mode = "profile"
```

`manual_app_group_associations` records a reviewed Account Holder/Admin
confirmation only when the public App Store Connect API cannot expose the App
Group relationship. It names aliases from `tasks.signing.app_groups` and applies
to every configured bundle rule that requires `APP_GROUPS`. It does not bypass
the exact App Group authorization check on every generated or reused profile.

Nested target IDs preserve the suffix below the source root unless a reviewed
`target_bundle_id` override is present. `unknown_profile_bundles = "error"`
prevents a newly added extension from silently inheriting another profile.

Entitlement modes are `profile`, `preserve-source`, and `template`. Templates
must live below `configs/signing` and may use only `${TEAM_ID}`,
`${APP_IDENTIFIER_PREFIX}`, `${TARGET_BUNDLE_ID}`, and named
`${APP_GROUP:<alias>}` placeholders. Intentional entitlement drops require an
explicit list and rationale.

The standard `LiveContainer.ipa` has four profile-bearing bundles. The
`LiveContainer+SideStore.ipa` variant adds `LiveWidget` and requires a separate
fifth App ID, profile, widget policy, App Group review, and device acceptance;
it is not a substitute asset for the standard task. Keep a new multi-bundle task
at `publication_enabled = false` until automated canary and device acceptance
both pass.

## Triggers

- **Scheduled**: Daily at 02:00 UTC (keeps cache fresh and auto-processes new releases)
- **Scheduled integration**: Weekly checksum-pinned LiveContainer inventory verification
- **Manual**: Workflow Dispatch inputs:
  - `debug` — Enable Cloudflare Tunnel for SSH debugging (default: `false`)
  - `force_rebuild` — Force full rebuild ignoring cache (default: `false`)
  - `package_shadow` — Run inventory and Apple planning without mutation
  - `backend_qualification` — Run the private backend qualification only
  - `qualification_apply` / `qualification_reset_names` — Qualification-only options; the credential-free dispatch guard rejects them unless `backend_qualification=true`
  - `multi_bundle_canary` — Run private Linux/macOS multi-bundle acceptance with publication disabled
- **Webhook**: `repository_dispatch` with type `sign_ipas`

Example `repository_dispatch` payload:

```json
{
  "event_type": "sign_ipas",
  "client_payload": {}
}
```

## How It Works

1. **Restore Cache**: Restores the digest-verified signing index and content-addressed signed artifacts from the last successful boundary
2. **Build qualified zsign**: Downloads checksum-pinned upstream source, applies the reviewed per-profile-entitlements patch, builds version `1.1.1+sideloadedipa.3`, verifies it, and reuses a source/patch-keyed CI cache. The backend signs from extracted private key and certificate material without a Keychain. Profile-free helper executables are signed without inheriting an enclosing app's entitlements
3. **Inventory and aggregate preflight**: `sideloadedipa inspect` selects current assets, inventories every executable recursively, and validates all selected task policies before any Apple mutation
4. **Plan Apple resources**: `sideloadedipa plan` performs a read-only reconciliation and records the canonical resource plan
5. **Sync package profiles**: `sideloadedipa sync --apply` via the typed App Store Connect adapters:
   - Reconciles selected task profiles against current devices, certificate, and capabilities
   - Downloads and validates every profile before signing
6. **Sign IPAs**: `sideloadedipa sign`:
   - Builds a complete fingerprint from source, graph, policy, Apple resources, profiles, certificate, devices, backend/tool versions, and schema
   - Selectively rebuilds changed tasks
   - Treats a matching cache record only as a reuse candidate and fully reopens and verifies its IPA before accepting the hit
   - Re-signs with `zsign` using the P12 certificate and all task profiles
7. **Verify IPAs**: `sideloadedipa verify --publish` independently reopens every output and verifies identifiers, profiles, entitlements, nested signatures, graph integrity, and package integrity
8. **Publish registry**: `sideloadedipa publish` re-verifies outputs, then:
   - Reads the signed IPA's actual bundle id + version, uploads the IPA to R2 under a versioned, immutable key (`apps/<slug>/<version>/<App>.ipa`)
   - Uploads the card icon under a content-addressed, immutable key (`apps/<slug>/icon-<sha12>.png`), so a changed icon lands on a fresh URL rather than waiting out the zone's 4-hour browser cache. The `no-transform` directive opts icons out of Cloudflare Polish, which otherwise re-encodes them lossily at the edge
   - Merges results into `site/apps.json` on R2, calls the Vercel `/api/revalidate` hook, then deletes stale referenced keys; failed batches remove only new unreferenced uploads
9. **Save Cache and evidence**: Promotes the cache only after verification/publication succeeds and uploads redacted stage manifests plus the complete run report

## Caching Behavior

The workflow uses GitHub Actions cache to minimize unnecessary work:

- **Cache Storage**: `work/cache/` contains `signing-index.json` plus content-addressed signed IPA artifacts

- **Cache Lifetime**: 7 days of inactivity (refreshed by daily scheduled runs)

- **Reuse Logic**: A complete fingerprint mismatch rebuilds only affected tasks. A match still requires current prerequisite checks and full independent IPA reopen verification; a rejected hit is rebuilt fail-closed.

- **Force Rebuild**: Use the `force_rebuild` input to bypass signing cache reuse and rebuild everything

## Requirements and Notes

- **Runner**: `ubuntu-latest` — `zsign` signs via OpenSSL (not Apple's `codesign`/Security.framework), so the whole pipeline runs on Linux (≈10× cheaper than a macOS runner)
- **Tools installed**: patched `zsign` (built from checksum-pinned source) and `asc` (checksum-verified release asset); `boto3` comes from `uv.lock`
- **Signing**: Uses the qualified [`zsign`](https://github.com/zhlynn/zsign) extension with one ordered profile/entitlement pair per profile-bearing bundle (no Linux Keychain / codesign identity required)
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
- Remove Apple, signing, GitHub, R2, and revalidation credentials from the long-lived SSH server, tunnel, and wait-process environments before the session opens.

Connect with the private key matching `DEBUG_SSH_PUBLIC_KEY`, tunnelling SSH through Cloudflare (end-to-end encrypted, no third-party SSH relay):

```bash
ssh -o ProxyCommand='cloudflared access ssh --hostname %h' runner@<printed-host>.trycloudflare.com
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
   # Run selected package tasks without publication
   uv run sideloadedipa run --run-id local-jhentai --task JHenTai --apply --json
   ```

4. **Run tests** (when available):
   ```bash
   uv run pytest
   ```

5. **Format code**:
   ```bash
   # Format with black
   uv run black src tests scripts

   # Sort imports with isort
   uv run isort src tests scripts

   # Type check with mypy
   uv run mypy src/sideloadedipa
   ```

### Why uv?

- **Fast**: 10-100x faster than pip
- **Reliable**: Lockfile ensures reproducible installs
- **Simple**: Single tool for virtual environments and dependencies
- **Compatible**: Works with standard `pyproject.toml`

## Latest Actions Versions

- `actions/checkout@v7.0.1`
- `astral-sh/setup-uv@v9.0.0`
- `actions/cache@v6.1.0`
- `actions/upload-artifact@v7.0.1`
- `actions/download-artifact@v8.0.1`

These are selected based on current docs and should be kept up to date.
