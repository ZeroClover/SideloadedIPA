# Migration Guide

This guide describes configuration and command changes required when upgrading existing SideloadedIPA setups.

---

## 1. Pinned SHA-256 for Direct IPA Sources

All tasks using `ipa_url` must specify the expected `ipa_sha256`:

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My App"
bundle_id = "com.example.myapp"
ipa_url = "https://example.com/MyApp.ipa"
ipa_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

Calculate the digest using:

```bash
shasum -a 256 MyApp.ipa
```

*(Note: Do not add `ipa_sha256` to tasks using `repo_url`; GitHub releases are validated automatically during download).*

---

## 2. Removed Obsolete Signing Options

The following keys in `[tasks.signing]` have been removed because their behaviors are now built-in standards:

```toml
# Remove these obsolete lines:
id_strategy = "preserve-source-suffix"
unknown_profile_bundles = "reject"
profile_type = "IOS_APP_DEVELOPMENT"
```

---

## 3. Standardized Package CLI

Replace legacy standalone scripts with the unified `sideloadedipa` CLI:

```bash
uv run sideloadedipa inspect --run-id <run-id> --task <task>
uv run sideloadedipa plan --run-id <run-id> --task <task>
uv run sideloadedipa sync --run-id <run-id> --task <task> --apply
uv run sideloadedipa sign --run-id <run-id> --task <task>
uv run sideloadedipa verify --run-id <run-id> --task <task>
uv run sideloadedipa publish --run-id <run-id> --task <task>
```

---

## 4. Header-Based Web Revalidation

Web cache revalidation now requires passing the secret via the `X-Revalidate-Secret` HTTP header rather than query parameters:

```bash
curl -f -s -H "X-Revalidate-Secret: $VERCEL_REVALIDATE_SECRET" \
  "https://itms.example.com/api/revalidate"
```

---

## 5. Backend Qualification Tool

Use the consolidated tool to qualify `zsign` and verify signing output:

```bash
uv run sideloadedipa-qualify-backend \
  --run-id "backend-$(date +%Y%m%d%H%M%S)" \
  --evidence work/qualification/backend-qualification.json
```

