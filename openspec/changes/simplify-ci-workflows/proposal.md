## Why

CI now mixes production publication with migration-only probes, backend qualification, private canaries, and duplicated validation jobs. The result is an 801-line production workflow with a combinatorial manual-dispatch interface, while PR validation still relies on a Ruby structural check that does not meaningfully audit composite actions.

## What Changes

- **BREAKING** Remove the standalone scheduled LiveContainer integration workflow; keep the checksum-pinned integration test available as an explicit local test.
- Reduce the production workflow to the daily/manual/webhook publication path and retain only the `debug` and `force_rebuild` manual inputs.
- Remove read-only shadow, Apple-state probe, backend-qualification, qualification mutation/reset, and non-publishing canary jobs and inputs. New apps are exercised through the verified production publication path instead of a private canary path.
- Keep SSH debug as a first-class manual troubleshooting capability after CI failures, with the existing credential and private-material cleanup boundaries.
- Consolidate Python, configuration, toolchain, workflow, composite-action, and web validation into one PR job.
- Replace the Ruby YAML structure probe with pinned `zizmor` analysis of both workflows and composite actions, while retaining `actionlint` for workflow expression and embedded-shell validation.
- Pin third-party GitHub Action references by immutable commit digest and remove the now-unused qualification-fixture composite action.

## Capabilities

### New Capabilities

- `ci-validation`: Defines the compact PR validation surface, real backend coverage, workflow/composite-action static analysis, immutable Action pins, and manual SSH troubleshooting behavior.

### Modified Capabilities

- `signing-workflow-orchestration`: Removes private canary and auxiliary dispatch modes, permits reviewed new tasks to use verified production publication for testing, and retains least-privilege SSH debug on the production path.

## Impact

- `.github/workflows/integration.yml` and `.github/actions/build-qualification-fixture/action.yml` are removed.
- `.github/workflows/pr-checks.yml` and `.github/workflows/sign-and-upload.yml` become materially smaller.
- `pyproject.toml` and `uv.lock` gain a pinned `zizmor` development tool.
- Workflow contract tests, README, security guidance, and the operator runbook are synchronized with the reduced CI surface.
