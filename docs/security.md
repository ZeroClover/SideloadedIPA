# Signing pipeline security

## Archive and workspace isolation

Every IPA is treated as untrusted input. Preflight rejects absolute, traversal,
NUL, duplicate-normalized, link, and special-file entries and enforces entry,
expanded-size, and compression-ratio limits before extraction. Each task uses an
isolated workspace; signing mutates a copy and promotes output atomically only
after independent verification. Temporary source, profile, certificate, key, and
extracted files are never artifact paths.

Subprocesses use argv arrays, `shell=False`, bounded output, explicit timeouts,
and allowlisted environments. Python's current security guidance confirms that
without an explicitly selected shell, shell metacharacters are passed as ordinary
characters: [Python subprocess security considerations](https://docs.python.org/3/library/subprocess.html#security-considerations).

## Credentials and logs

- Store the P12, P12 password, App Store Connect key, R2 credentials, revalidation
  secret, and optional debug public key only as GitHub Actions secrets.
- Inject signing and Apple credentials only into jobs that require them. The
  read-only shadow omits R2 credentials; the canary omits every publication
  credential.
- Never print secret values, private paths, raw profile payloads, P12 bytes, or
  private keys. Structured reports contain stable resource IDs and hashes only.
- GitHub warns that automatic masking is not guaranteed for transformed values,
  so application-level redaction remains mandatory:
  [secure use reference](https://docs.github.com/en/actions/reference/security/secure-use).
- SSH debug is manual-dispatch only, public-key only, time-bounded by the job,
  and must be cancelled immediately after diagnosis.

## CI artifacts and caches

Retain run reports for 7 days, shadow reports for 3 days, and canary comparison
reports for 1 day. These artifacts exclude IPAs and private material. GitHub
supports per-artifact retention and deletes artifacts with their workflow run:
[workflow artifact retention](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts#setting-the-retention-period-for-an-artifact).

Caches contain only reproducible non-secret state and use a versioned fingerprint.
Restored cache data is untrusted and cannot bypass profile freshness or output
verification. Save occurs only after successful signing/verification. GitHub
explicitly says caches must not contain credentials and may be readable from pull
request contexts:
[dependency cache security](https://docs.github.com/en/actions/concepts/workflows-and-actions/dependency-caching#cache-security).

## Apple mutation boundary

Apple operations use the documented App Store Connect API through the pinned CLI.
CI may perform exact lookup and additive, idempotent creation or capability
enablement only. Portal-only, approval-gated, ambiguous, destructive, or
undocumented operations are manual. There is no browser automation or private API
fallback. Apple documents capability setup and profile regeneration requirements
in its [capabilities overview](https://developer.apple.com/help/account/capabilities/capabilities-overview)
and [profile guidance](https://developer.apple.com/help/account/provisioning-profiles/edit-download-or-delete-profiles).

## Dependency and tool integrity

- Python dependencies come from committed `uv.lock`; CI uses `uv sync --frozen`.
  uv documents that frozen sync treats the lockfile as the source of truth:
  [locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).
- zsign, its reviewed extension source, App Store Connect CLI, actionlint, and
  cloudflared are version-pinned and checksum-verified before use.
- GitHub Actions are pinned to reviewed stable major/minor tags in the workflow
  and checked by PR workflow tests. Review release notes and published checksums
  before every update.

## Rotation

Rotate an exposed or departing-operator App Store Connect key, R2 token,
revalidation secret, debug key, or P12 immediately. For planned rotation:

1. Add the replacement with least privilege and leave the current credential
   active.
2. Run credential verification, read-only planning, and a non-publishing canary.
3. Replace the GitHub secret and confirm a new run uses the expected public
   certificate/resource fingerprint.
4. Revoke the old credential only after the new path passes. A certificate change
   requires replacement profiles and invalidates related signing caches.
