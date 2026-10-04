# Runtime and credential environment

Read when configuring a stage or web deployment. Never collect or print secret
values to diagnose setup; inspect names and redacted errors instead.

## Loading local values

.env.example lists placeholder names. The Python CLI does **not** load .env
automatically: supply values through the invoking process's environment or an
approved secret manager. Keep .env and configs/tasks.local.toml untracked. Do not
source an untrusted environment file. Tests normally use fixtures, not these values.

Authoritative consumers: src/sideloadedipa/pipeline/environment.py,
src/sideloadedipa/adapters/apple/asc.py, src/sideloadedipa/adapters/publication/r2_store.py,
and .github/workflows/sign-and-upload.yml.

| Variable | Consumer / meaning |
| --- | --- |
| ASC_KEY_ID, ASC_ISSUER_ID | Apple API identity |
| ASC_PRIVATE_KEY_B64 | Base64 P8 private key; production workflow maps the ASC_PRIVATE_KEY secret to this variable |
| ASC_BYPASS_KEYCHAIN | Headless ASC authentication (CI sets 1) |
| APPLE_DEV_CERT_P12_ENCODED | Base64 Apple Development P12 |
| APPLE_DEV_CERT_PASSWORD | P12 password |
| GITHUB_TOKEN | GitHub release API authentication |
| ZSIGN_BIN, ZSIGN_SHA256 | Qualified patched backend path and verified binary digest |
| R2_ACCOUNT_ID | R2 endpoint account |
| R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY | Bucket-scoped object read/write credentials |
| R2_BUCKET, R2_PUBLIC_BASE_URL | Bucket and public artifact base URL |
| R2_REGION | Explicit S3 region hint; defaults to auto |
| VERCEL_REVALIDATE_SECRET | Publisher's refresh secret |
| VERCEL_REVALIDATE_URL | Optional publisher hook override; default in pipeline/environment.py |
| DEBUG_SSH_PUBLIC_KEY | Manual CI debug authentication, not a private key |

Planning can require Apple reads and P12 certificate identity even though it makes
no Apple mutations. Inspection downloads sources and writes local evidence: read-only
means no production resource mutation, not no network or local filesystem writes.

## Web deployment

Consumers: web/lib/apps.ts, web/lib/site.ts, web/app/api/revalidate/route.ts.

| Variable | Meaning |
| --- | --- |
| APPS_DATA_MODE | Explicit fixture for local/CI builds or origin for real data |
| R2_APPS_JSON_URL | HTTPS catalog URL required in origin mode |
| REVALIDATE_SECRET | Web-side secret; equals publisher VERCEL_REVALIDATE_SECRET |
| SITE_PUBLIC_BASE_URL | Stable public site base for install manifests |
| VERCEL_PROJECT_PRODUCTION_URL | Stable production alias if explicit site base is unset |

Fixture mode is rejected when VERCEL_ENV is production. Do not use per-deploy
VERCEL_URL for OTA links: deployment protection and changing hosts break appstored
access. Revalidation is authenticated POST; see [Publication](publication.md).
