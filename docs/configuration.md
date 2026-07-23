# Configuration reference

The package reads `configs/tasks.toml` by default. Pass `--config <path>` to use a
different file. `configs/tasks.toml.example` is the maintained example and is safe
to copy for local editing.

## Task fields

Each `[[tasks]]` entry has these common fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `task_name` | yes | Stable operator and profile lookup name. |
| `app_name` | yes | Human-readable name and default profile-name stem. |
| `bundle_id` | yes | Target root bundle identifier. |
| `ipa_url` or `repo_url` | yes | Exactly one source form. |
| `slug` | no | Stable R2/registry key; defaults to a sanitized `app_name`. |
| `icon_path` | no | Repository-relative asset, HTTPS URL, or `ipa:`. |
| `publication_enabled` | no | Explicit publication gate; defaults to `false`. |

A repository-relative `icon_path` is valid only for a GitHub source. An HTTPS icon
URL works with either source form. `ipa:` extracts the icon from the signed IPA.

## Source identity

An immutable direct source requires both fields:

```toml
ipa_url = "https://downloads.example/MyApp.ipa"
ipa_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

The URL must be HTTPS and contain no embedded credentials. The digest must be 64
hexadecimal characters and match the exact reviewed bytes. Compute it with
`shasum -a 256 MyApp.ipa`. Changing the URL or digest changes the source and cache
fingerprints.

A GitHub release source uses:

```toml
repo_url = "https://github.com/owner/repository"
release_glob = "MyApp.ipa"
use_prerelease = false
```

`release_glob` defaults to `*.ipa`, but a release containing multiple IPA assets
should use an exact selector. Zero or multiple matches fail closed. Do not set
`ipa_sha256` with `repo_url`; the pipeline binds the selected GitHub asset identity,
advertised size/digest when available, and measured digest.

All production downloads use the package-owned byte, timeout, chunk, redirect,
and retry policy. Those safety limits are not per-task options.

## Multi-bundle signing

Add `[tasks.signing]` when the IPA contains profile-bearing nested bundles or needs
an explicit entitlement policy. Every discovered profile-bearing bundle must have
exactly one `[[tasks.signing.bundles]]` rule.

```toml
[tasks.signing]
manual_app_group_associations = ["shared"]

[tasks.signing.app_groups]
shared = "group.com.example.myapp"

[[tasks.signing.bundles]]
source_bundle_id = "com.upstream.MyApp"
target_bundle_id = "com.example.myapp"
role = "root"
required_capabilities = ["APP_GROUPS"]
entitlement_mode = "template"
entitlements_file = "configs/signing/myapp/root.plist"
```

Bundle-rule fields are:

| Field | Required | Meaning |
| --- | --- | --- |
| `source_bundle_id` | yes | Exact identifier found in the unsigned graph. |
| `target_bundle_id` | no | Explicit target; otherwise the source suffix is preserved under the target root. |
| `role` | no | Reviewed semantic label such as `root`. |
| `required_capabilities` | no | Allowlisted Apple capabilities required by this bundle. |
| `entitlement_mode` | no | `profile` (default), `preserve-source`, or `template`. |
| `entitlements_file` | for `template` | Repository-controlled plist below `configs/signing`. |
| `allowed_entitlement_drops` | no | Explicit keys that may be removed. |
| `drop_rationale` | with drops | Required human rationale for every declared drop set. |

Entitlement modes behave as follows:

- `profile` uses the mapped provisioning profile entitlement document.
- `preserve-source` rewrites reviewed team, identifier, and App Group values while
  preserving remaining source values.
- `template` loads a plist below `configs/signing` and permits only
  `${TEAM_ID}`, `${APP_IDENTIFIER_PREFIX}`, `${TARGET_BUNDLE_ID}`, and
  `${APP_GROUP:<alias>}` placeholders.

`manual_app_group_associations` lists aliases whose Portal relationship has been
reviewed manually because the public API cannot inspect it. It does not bypass
profile authorization checks.

Do not configure `id_strategy`, `unknown_profile_bundles`, or `profile_type`.
Preserve-source-suffix mapping, rejection of uncovered profile-bearing bundles,
and iOS development profiles are fixed safety invariants.

## Publication layout

Publication is disabled per task unless `publication_enabled = true`. Optional
root tables control only stable layout/policy choices:

```toml
[r2]
key_prefix = "apps"
apps_json_key = "site/apps.json"

[publication]
batch_policy = "atomic"
```

`batch_policy` accepts `atomic` or `independent`; production configuration should
use the reviewed policy for the deployment. Credentials, bucket identity, and
public origins never belong in this TOML file.

## Pipeline environment

Export only the categories required by the stage being run. `.env.example` shows
the current variable names and encoding formats.

- Apple planning/synchronization: `ASC_KEY_ID`, `ASC_ISSUER_ID`, and one supported
  private-key input, with `ASC_BYPASS_KEYCHAIN=1` for headless operation.
- Certificate/signing: `APPLE_DEV_CERT_P12_ENCODED`,
  `APPLE_DEV_CERT_PASSWORD`, `ZSIGN_BIN`, and the exact `ZSIGN_SHA256`.
- R2 publication: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`, and optional
  `R2_REGION`.
- Revalidation: `VERCEL_REVALIDATE_SECRET` and optional
  `VERCEL_REVALIDATE_URL` in the pipeline.
- CLI behavior: `GITHUB_RUN_ID` is the default run ID. Use the explicit
  `--config` option for a non-default task file.

Keep all credential values in the CI secret store. The operator runbook explains
which stage receives each category and how to rotate it.

## Web deployment environment

The Next.js application has two explicit registry modes:

- Validation/local build: `APPS_DATA_MODE=fixture`.
- Production: `APPS_DATA_MODE=origin`, HTTPS `R2_APPS_JSON_URL`,
  `REVALIDATE_SECRET`, and `SITE_PUBLIC_BASE_URL`.

`VERCEL_ENV=production` rejects fixture mode. The revalidation endpoint accepts
the shared secret only through `X-Revalidate-Secret`; query-string credentials are
not supported. The registry decoder rejects malformed entries, duplicate slugs,
and non-HTTPS IPA/icon URLs before rendering a page or plist.
