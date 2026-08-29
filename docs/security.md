# Security Model

This document outlines the security architecture and protections built into the SideloadedIPA pipeline.

---

## 1. Archive & Workspace Isolation

- **Sanitized Extraction**: All IPAs are treated as untrusted archives. The preflight extractor strictly rejects absolute paths, directory traversal sequences (`../`), NUL bytes, symlinks, and excessive compression ratios (zip bombs).
- **Isolated Workspaces**: Each task executes in an isolated temporary directory. Files are cleaned up automatically after run completion or cancellation.
- **Safe Subprocesses**: Subprocess commands are executed directly via argument arrays (`shell=False`) with strict timeouts and allowlisted environment variables, preventing shell injection vulnerabilities.

---

## 2. Credential Management & Log Redaction

- **Scoped Secrets**: Sensitive secrets (Apple Developer certificates, App Store Connect keys, Cloudflare R2 tokens, and Vercel revalidation secrets) are stored exclusively in GitHub Actions Secrets and injected only into the specific steps that require them.
- **Automatic Log Redaction**: The pipeline redacts private keys, certificate passwords, and raw profile payloads from terminal logs, stage manifests, and run reports.
- **Minimal Report Footprint**: Uploaded run reports contain only non-sensitive metadata: execution timings, commit hashes, stable resource IDs, and SHA-256 digests.

---

## 3. Apple Developer Operations

- **Additive-Only Mutations**: The automated sync stage creates missing App IDs and provisioning profiles, but **never deletes** existing developer resources or revokes certificates.
- **Official API Usage**: Interacts with Apple Developer services exclusively through the official App Store Connect API via the checksum-verified `asc` CLI. No undocumented endpoints or browser automation are used.

---

## 4. Supply Chain & Toolchain Integrity

- **Locked Python Dependencies**: Python packages are strictly locked via `uv.lock` and installed with `uv sync --frozen`.
- **Checksum Verification**: External binaries (`zsign`, `asc`, `cloudflared`, and `actionlint`) are downloaded and verified against exact SHA-256 digests before execution.
- **Pinned Actions**: GitHub Actions workflows reference immutable commit SHAs with automated security audits via `zizmor` and `actionlint`.

---

## 5. SSH Debugging Protections

- **Manual Trigger Only**: The SSH debug helper can only be invoked via `workflow_dispatch` with an explicit `debug: true` flag.
- **Public-Key Authentication**: Only the public key specified in `DEBUG_SSH_PUBLIC_KEY` is authorized; password authentication is disabled.
- **Ephemeral Session**: The connection is routed through an ephemeral Cloudflare Tunnel and terminates automatically when the CI job timeout expires.

