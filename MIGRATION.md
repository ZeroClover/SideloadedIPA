# Current migration guide

This file covers only migrations that a currently supported configuration,
automation caller, or operator command may still require. Completed project plans
and retired infrastructure history remain available in Git history.

## Pin direct IPA sources

Every task using `ipa_url` must use HTTPS and declare the canonical SHA-256 of the
reviewed IPA bytes:

```toml
[[tasks]]
task_name = "MyApp"
app_name = "My App"
bundle_id = "com.example.myapp"
ipa_url = "https://downloads.example/MyApp.ipa"
ipa_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

Calculate the value from the exact reviewed file:

```bash
shasum -a 256 MyApp.ipa
```

Do not add `ipa_sha256` to a `repo_url` task. GitHub release tasks use the selected
release asset's advertised identity and the digest measured during download.

## Remove redundant signing keys

Delete these keys from every `[tasks.signing]` table:

```toml
id_strategy = "preserve-source-suffix"
unknown_profile_bundles = "reject"
profile_type = "IOS_APP_DEVELOPMENT"
```

Their former values are now fixed package invariants: target identifiers preserve
the source suffix, uncovered profile-bearing bundles fail closed, and provisioning
uses iOS development profiles. The parser rejects each obsolete key with a
field-specific removal message; there is no replacement value to choose.

## Use the package CLI

Replace retired script entry points with the installed package command:

```bash
uv run sideloadedipa inspect --run-id <run-id> --task <task>
uv run sideloadedipa plan --run-id <run-id> --task <task>
uv run sideloadedipa sync --run-id <run-id> --task <task> --apply
uv run sideloadedipa sign --run-id <run-id> --task <task>
uv run sideloadedipa verify --run-id <run-id> --task <task>
uv run sideloadedipa publish --run-id <run-id> --task <task>
```

Reuse the same unique `--run-id` for all stages of one attempt. Do not copy stage
files between run IDs; downstream commands validate the predecessor chain.

## Use header-authenticated web revalidation

Query-string secrets are unsupported. Send the shared secret only in the
`X-Revalidate-Secret` request header:

```bash
curl --fail --silent --show-error \
  --header "X-Revalidate-Secret: $VERCEL_REVALIDATE_SECRET" \
  "https://itms.example/api/revalidate"
```

Set the same value as `REVALIDATE_SECRET` in the web deployment. Rotate any secret
that was previously placed in a URL because URLs can be retained by logs, browser
history, and intermediaries.

## Use the consolidated backend qualification command

Replace qualification wrappers, prerequisite/reset commands, and direct fixture
drivers with:

```bash
uv run sideloadedipa-qualify-backend \
  --run-id "backend-$(date +%Y%m%d%H%M%S)" \
  --evidence work/qualification/backend-qualification.json
```

The command uses production inspect/plan/sync behavior and has no destructive
reset mode. See the [operator runbook](docs/operator-runbook.md#backend-requalification)
for required zsign, Apple, and macOS oracle inputs.
