## 1. Production CI Surface

- [x] 1.1 Delete the standalone integration workflow and unused qualification-fixture composite action while retaining local diagnostic and integration tooling.
- [x] 1.2 Reduce `sign-and-upload.yml` to its production job with only `debug` and `force_rebuild` manual inputs, preserving schedule, webhook, cache, evidence, notification, and SSH behavior.
- [x] 1.3 Update workflow contract tests to enforce the minimal production dispatch surface and retained SSH isolation.

## 2. Pull-request Validation

- [x] 2.1 Consolidate Python, ASC/toolchain, workflow, and web checks into one PR validation job with one terminal SSH debug step.
- [x] 2.2 Add pinned zizmor validation for workflows and composite actions, remove the Ruby shape check, and lock the tool dependency.
- [x] 2.3 Pin every external Action reference to an immutable commit digest with readable version comments and test the pin contract.

## 3. Documentation and Acceptance

- [x] 3.1 Reconcile README, security guidance, and the operator runbook with direct production testing and the reduced manual inputs.
- [x] 3.2 Run Python tests/coverage, formatting, strict typing, actionlint, zizmor, web tests/build, OpenSpec strict validation, and diff checks.
