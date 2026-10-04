# Task and signing configuration

Read when editing task TOML or entitlement templates. Schema authority is
`src/sideloadedipa/config/parser.py` and `src/sideloadedipa/domain/config.py`; schema examples are
`configs/tasks.toml.example` and `tests/fixtures/configuration/signing-cases.toml`.
Credentials are documented separately in [Environment](environment.md).

SideloadedIPA reads its task definitions from `configs/tasks.toml` by default (or from `--config <path>`). You can copy `configs/tasks.toml.example` to `configs/tasks.local.toml` as a starting template.

---

## Basic Task Configuration

Define tasks using the `[[tasks]]` array:

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My Application"
bundle_id = "com.example.myapp"
repo_url = "https://github.com/example/MyApp"
release_glob = "MyApp.ipa"
publication_enabled = false  # Enable only after real-device acceptance
```

### Core Task Fields

| Field | Required | Description |
| --- | --- | --- |
| `task_name` | Yes | Unique identifier used for CLI commands and profile naming. |
| `app_name` | Yes | Human-readable app name displayed on the web portal. |
| `bundle_id` | Yes | Target bundle identifier for the main application. |
| `ipa_url` or `repo_url` | Yes | Source of the IPA (choose direct URL or GitHub repository). |
| `slug` | No | URL slug for R2 storage and web routing (defaults to sanitized `app_name`). |
| `icon_path` | No | App icon source: `"ipa:"` (extracts from IPA), relative repo path (GitHub sources only), or HTTP/HTTPS URL. Prefer HTTPS. |
| `publication_enabled` | No | Set `true` to allow uploading and publishing to R2 (default: `false`). |

---

## Source Definitions

### Option 1: Direct HTTPS Download

Direct URLs require pinning the expected SHA-256 checksum:

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My App"
bundle_id = "com.example.myapp"
ipa_url = "https://example.com/downloads/MyApp.ipa"
ipa_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

> Compute the hash locally with `shasum -a 256 MyApp.ipa`.

### Option 2: GitHub Release Tracking

Automatically tracks releases from a GitHub repository:

```toml
[[tasks]]
task_name = "LiveContainer"
app_name = "LiveContainer"
bundle_id = "io.example.livecontainer"
repo_url = "https://github.com/LiveContainer/LiveContainer"
release_glob = "LiveContainer.ipa"  # Pattern matching the target asset (default: "*.ipa")
use_prerelease = false             # Set true to track beta / pre-releases
```

---

## Multi-Bundle Signing & App Extensions

If an IPA includes app extensions (`.appex`) or embedded helper binaries, configure `[tasks.signing]` with explicit bundle rules:

```toml
[[tasks]]
task_name = "LiveContainer"
app_name = "LiveContainer"
bundle_id = "io.example.livecontainer"
repo_url = "https://github.com/LiveContainer/LiveContainer"
release_glob = "LiveContainer.ipa"

[tasks.signing.app_groups]
shared = "group.io.example.livecontainer"

# Main App
[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer"
target_bundle_id = "io.example.livecontainer"
role = "root"
required_capabilities = ["APP_GROUPS", "HEALTHKIT", "INCREASED_MEMORY_LIMIT", "KEYCHAIN_SHARING"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/root-process.plist"

# Helper Process
[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.LiveProcess"
required_capabilities = ["APP_GROUPS", "HEALTHKIT", "INCREASED_MEMORY_LIMIT", "KEYCHAIN_SHARING"]
entitlement_mode = "template"
entitlements_file = "configs/signing/livecontainer/root-process.plist"

# App Extension
[[tasks.signing.bundles]]
source_bundle_id = "com.kdt.livecontainer.LaunchAppExtension"
required_capabilities = ["APP_GROUPS"]
entitlement_mode = "profile"
```

### Bundle Fields

| Field | Required | Description |
| --- | --- | --- |
| `source_bundle_id` | Yes | Original Bundle ID found in the unsigned IPA. |
| `target_bundle_id` | No | Explicit target Bundle ID. If omitted, preserves the suffix under the root bundle ID. |
| `role` | No | Semantic role (e.g. `root`, `extension`). |
| `required_capabilities` | No | Apple capabilities required by this bundle (e.g., `APP_GROUPS`, `HEALTHKIT`). |
| `entitlement_mode` | No | `profile` (default), `template`, or `preserve-source`. |
| `entitlements_file` | With `template` | Path to the plist template file under `configs/signing/`. |
| `allowed_entitlement_drops` | No | Reviewed entitlement keys permitted to be dropped; not a verification bypass. |
| `drop_rationale` | With drops | Explanation for why entitlements were dropped. |

### Entitlement Modes

- **`profile`**: Uses the entitlements authorized directly by the generated provisioning profile.
- **`template`**: Injects a custom plist template with variables (`${TEAM_ID}`, `${APP_IDENTIFIER_PREFIX}`, `${TARGET_BUNDLE_ID}`, `${APP_GROUP:<alias>}`).
- **`preserve-source`**: Preserves original entitlements while remapping identity-dependent values. The profile must still authorize the result.

Treat loss of functional entitlements as a failure unless the exact drop is reviewed
and has `drop_rationale`. `manual_app_group_associations = ["shared"]` under
`[tasks.signing]` records reviewed manual confirmation for a configured alias; it
does not bypass exact App Group authorization in the profile.

The obsolete `[tasks.signing]` keys `id_strategy`, `unknown_profile_bundles`, and
`profile_type` are rejected: suffix mapping, unknown-bundle rejection, and
`IOS_APP_DEVELOPMENT` profiles are fixed invariants. Do not add compatibility parsing.

---

## Global Storage & Publishing Settings

Optionally customize Cloudflare R2 object keys and publication behavior:

```toml
[r2]
key_prefix = "apps"              # Prefix for uploaded IPAs and icons
apps_json_key = "site/apps.json" # Central registry file read by the web front-end

[publication]
batch_policy = "atomic"          # "atomic" (all or nothing) or "independent"
```

---

## Validate without external effects

Use `load_configuration(Path(...))` from `sideloadedipa.config.parser` or the
configuration tests; `inspect` also downloads sources and is not a parsing-only
command. Examples use illustrative upstream URLs and hashes, not trusted artifacts.

```bash
uv run --frozen pytest --no-cov tests/test_config_parser.py tests/test_signing_config_fixtures.py tests/test_entitlement_templates.py
```

Use [Operations](operator-runbook.md) for stage commands and
[Publication](publication.md) before enabling publication or changing storage keys.
