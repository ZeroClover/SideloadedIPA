# Apple State Probe Evidence

Date: 2026-07-21

## Scope

Task 6.2 was exercised against the configured App Store Connect credentials on
the development branch. The workflow path is read-only: it lists Bundle IDs,
exposed capabilities, development certificates, enabled iOS devices, and iOS
development profiles plus their relationship linkages. It does not create,
update, or delete Apple resources and does not run signing or publication jobs.

The probe installs checksum-verified App Store Connect CLI 3.1.1 and emits only
resource counts and a digest of the normalized, redacted snapshot. Raw device
UDIDs, certificate/profile content, and credentials are not logged or retained
as artifacts.

## Contract Findings

- Run [29849742206](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29849742206)
  established that an empty paginated `data` collection is encoded as JSON
  `null` by ASC 3.1.1. The collector now normalizes that documented empty state
  without weakening validation for other shapes.
- Run [29849867036](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29849867036)
  and run [29850313573](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29850313573)
  exposed successful JSON responses larger than the shared diagnostic capture
  limit. Successful stdout is now preserved in full for structured parsing;
  failure stdout/stderr remain bounded and redacted. Profile relationships are
  read through the dedicated `profiles links` commands.

## Accepted Run

[Run 29850439272](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29850439272)
completed successfully in 31 seconds. All mutation, signing, qualification, and
publication jobs were skipped.

Redacted result:

```json
{"counts":{"bundle_ids":21,"capabilities":33,"certificates":9,"devices":11,"profiles":13},"schema_version":1,"snapshot_sha256":"6c3e48e893998aba335735162e8a68e4c4f2243cd00b6f6eaae0c4608d8a86ec"}
```

Local acceptance for the final implementation:

- `uv run pytest -q`: 448 passed, 2 skipped, 96.12% package coverage.
- `uv run mypy src/sideloadedipa`: passed.
- package Black and isort checks: passed.
- `openspec validate add-multi-bundle-ipa-signing --strict`: passed.

## Certificate Identity Probe

[Run 29852172410](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29852172410)
used the configured P12 and password only inside the read-only job. The P12
certificate SHA-256 matched exactly one active Apple development certificate,
resource `L7C8RNH3A3`, expiring at `2026-08-25T06:59:45+00:00`. The probe
reported only the stable resource ID, public certificate/public-key hashes,
serial number, and expiry. No P12, password, private key, or raw certificate was
retained as an artifact.
