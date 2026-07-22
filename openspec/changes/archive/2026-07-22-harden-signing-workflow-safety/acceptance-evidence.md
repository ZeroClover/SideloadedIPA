# Acceptance Evidence

## Local acceptance

Completed on 2026-07-22 against commit `63f1dcee73ceb60e1a499b440976184c25bbb4af`:

- `uv run pytest`: 722 passed, 2 skipped, 95.07% total coverage.
- Black and isort checks passed for scripts, package, and tests.
- mypy passed for the production package.
- Workflow static tests passed, including secret-scope, cleanup-order, and canary-command assertions.
- Web tests and the Next.js production build passed.
- actionlint 1.7.12 passed for every workflow after verifying the official release checksum.
- `openspec validate harden-signing-workflow-safety --strict` passed.
- `openspec validate --specs --strict` passed for all 11 specifications.

## Credentialed manual canary

GitHub Actions run [29890481504](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29890481504) completed successfully on 2026-07-22 against the same commit.

- The Linux qualification job passed the synthetic four-bundle negative control and qualified zsign backend before invoking the production CLI.
- `sideloadedipa run --task LiveContainer --apply --run-id 29890481504 --json` completed with `status=passed` and produced the retained run report.
- The run report contains exactly one successful `LiveContainer` task, `verification.passed=true`, and `publication=null`.
- The production signing plan contains 11 nodes, including the root app and three extensions mapped to four distinct provisioning profiles.
- All eight executed production stages from source through verify succeeded with measured non-zero durations; no publish stage ran.
- The installable IPA SHA-256 is `444cad094e686577c036afeb6ffbe04ba45feb54f1e9d85e379962db5751862a`, matching the production verification report.
- The independent macOS codesign oracle passed nested-signature verification and XML/DER entitlement checks for launch, process, share, and root roles.
- The Linux/macOS comparison passed profile mapping, root-last ordering, XML/DER evidence completeness, and both backend contracts.
- The workflow removed decoded private signing material before the optional SSH debug step and did not expose publication credentials to the canary.

The CI artifact `livecontainer-device-canary-29890481504` retains the private installable IPA for authorized device diagnosis. Physical install-and-launch acceptance had already passed for this signing configuration; per the approved time-blocked-task policy, that last verified device result remains the physical-observation evidence for this corrective change.
