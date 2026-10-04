# SideloadedIPA

SideloadedIPA automates downloading, Apple development signing, verification, and
OTA distribution of selected iOS IPA releases.

## What it does

- Tracks GitHub releases or direct HTTPS sources pinned by SHA-256.
- Signs root apps, nested apps/extensions, frameworks, and dylibs with explicit
  bundle policies and per-bundle provisioning profiles where required.
- Reconciles App IDs, capabilities, and development profiles through App Store
  Connect; operations unavailable through documented APIs remain manual prerequisites.
- Independently checks signatures, profiles, and XML/DER entitlements before publication.
- Reuses verified signing artifacts when their inputs have not changed.
- Publishes immutable IPA/icon objects and a catalog to Cloudflare R2, with a
  Next.js installation site hosted on Vercel.

## What you need

An Apple Developer account, an Apple Development signing certificate, registered
eligible devices, and App Store Connect credentials are required for real signing.
Development-signed IPAs are not unrestricted public App Store distribution.
Publication additionally needs Cloudflare R2 and a configured web deployment.

For local development, use Python and Node versions from .python-version and
web/.node-version, uv as pinned in pyproject.toml, and npm.

## Getting started

```bash
uv sync --frozen
cp configs/tasks.toml.example configs/tasks.local.toml
```

Edit the local task file to select the source and target bundle identifiers. Start
with source inspection and resource planning before applying real changes. The
production workflow is .github/workflows/sign-and-upload.yml; the web app is in web/.

The [documentation map](docs/index.md) links configuration, environment, operations,
and development references. Those documents are task-oriented Agent references,
not an additional introductory manual. Agent instructions start in
[AGENTS.md](AGENTS.md); Claude uses [CLAUDE.md](CLAUDE.md).

## License

[GNU AGPL v3](LICENSE)
