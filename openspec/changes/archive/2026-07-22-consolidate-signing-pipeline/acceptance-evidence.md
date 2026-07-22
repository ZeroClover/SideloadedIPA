# Acceptance Evidence

Completed on 2026-07-22 against merged `master` commit
`2b2bfa55eb22ba5eebee64dee6ed4c1b586d25a2`.

## Credentialed non-publishing canary

GitHub Actions run [29929591634](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29929591634)
completed successfully.

- The credential-free dispatch guard passed before the credential-bearing jobs started.
- The private Linux canary reconciled the real Apple prerequisites, passed the upstream
  negative control, and passed the qualified patched-zsign backend contract.
- `sideloadedipa run --task LiveContainer --apply --run-id 29929591634 --json` completed
  successfully; all eight production stages from source through verify succeeded.
- The retained run report records `verification.passed=true`, `publication=null`, report
  SHA-256 `ada02bb9c830e692d9ada902d5a1b733322df9694a8132b417eccf1d53ebd94f`, and verified
  artifact SHA-256 `75e8bcd228a5a6439046c9ae906734d786a56467487b8838f0b46990aca3de10`.
- The independent macOS codesign oracle passed, and the final Linux/macOS comparison passed
  profile mapping, root-last ordering, XML/DER evidence completeness, both backend contracts,
  and the negative control.
- The workflow retained only redacted qualification evidence and run reports; the signed IPA
  was not uploaded and the publishing job was skipped.

## Scheduled integration workflow

GitHub Actions run [29929594846](https://github.com/ZeroClover/SideloadedIPA/actions/runs/29929594846)
completed successfully through the scheduled integration workflow's manual acceptance trigger.
The user explicitly accepted this `workflow_dispatch` execution as the initial completion
evidence for the scheduled workflow.

- The job set `SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION=1` and ran
  `uv run pytest --no-cov -m integration tests/test_livecontainer_integration.py`.
- Both checksum-pinned LiveContainer integration tests passed (`2 passed in 2.26s`).
- Checkout ran with persisted repository credentials disabled, and the workflow used only
  read-only repository permissions.
