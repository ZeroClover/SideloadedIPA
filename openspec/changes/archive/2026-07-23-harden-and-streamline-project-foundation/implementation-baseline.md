# Implementation Baseline Evidence

Captured on 2026-07-23 before implementation changes. No production command,
credentialed operation, Apple mutation, signing, publication, or R2 update was
run while collecting this evidence.

## Effective prerequisite baseline

- `simplify-ci-workflows` is archived at
  `openspec/changes/archive/2026-07-22-simplify-ci-workflows/`.
- Its `ci-validation` delta is present in `openspec/specs/ci-validation/spec.md`.
- Its orchestration delta is present in
  `openspec/specs/signing-workflow-orchestration/spec.md`.
- The only workflow entry points are `.github/workflows/pr-checks.yml` and
  `.github/workflows/sign-and-upload.yml`; the removed standalone scheduled
  integration workflow is not present.

## Pre-change acceptance results

The local baseline used Python 3.14.6, Node.js 22.20.0, npm 11.6.2, uv 0.11.29,
actionlint 1.7.12, and zizmor 1.28.0. These are observations, not the selected
repository contracts recorded later in this document.

| Boundary | Command | Pre-change result |
| --- | --- | --- |
| Frozen Python install | `uv sync --frozen` | Passed; 33 packages checked. |
| Python tests and coverage | `uv run --frozen pytest` | Passed; 684 passed, 3 opt-in tests skipped, 95.03% package coverage. The baseline default also generated `htmlcov`. |
| Python formatting | `uv run --frozen black --check scripts/ src/sideloadedipa/` | Passed; 102 files unchanged. |
| Import order | `uv run --frozen isort --check-only scripts/ src/sideloadedipa/` | Passed. |
| Strict package typing | `uv run --frozen mypy src/sideloadedipa` | Passed; 101 files. |
| Strict script typing | `uv run --frozen mypy scripts/` | Passed; 1 file. |
| Frozen web install | `(cd web && npm ci)` | Passed; lock installed without modification. |
| Web tests | `(cd web && npm test)` | Passed; both plist golden cases byte-identical. |
| Production web build | `(cd web && npm run build)` | Passed; baseline emitted the known implicit bundled-fixture fallback warning. |
| Actions syntax/static shell | `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 -no-color .github/workflows/*.yml` | Passed. |
| Actions security analysis | `uv run --frozen zizmor --strict-collection --min-severity high .` | Passed offline with no reportable finding and 25 suppressions. |
| Change validation | `openspec validate harden-and-streamline-project-foundation --strict` | Passed. |
| Repository OpenSpec validation | `openspec validate --all --strict` | Passed; 13 items. |
| Python dependency audit | `uv audit --frozen` | Failed as the known pre-change baseline: 8 findings across Black 25.12.0, Click 8.3.1, Pygments 2.19.2, and pytest 9.0.2; compatible fixed releases were reported for all. |
| npm dependency audit | `(cd web && npm audit --audit-level=high)` | Failed as the known pre-change baseline: Next.js and sharp high-severity findings plus a moderate PostCSS finding; compatible fixes were reported. |
| Whitespace validation | `git diff --check` | Passed. |

The validation commands did not rewrite either lockfile. Their pre-change Git
blob hashes were:

- `uv.lock`: `f32a71d9bc9fee5cecd9a93e0f2d9313711e20cc`
- `web/package-lock.json`: `2a409a13bdf140599b3297f28ee69bbea0e9cc7c`

## Migration and consumer inventory

### Direct-source configuration

- `configs/tasks.toml`, the production operator configuration, contains seven
  GitHub-release tasks and no direct `ipa_url` task.
- `configs/tasks.toml.example` contains the only repository operator-facing
  direct-source example. It uses the placeholder HTTPS URL
  `https://example.com/path/to/MyApp.ipa` and currently has no checksum.
- `README.md` repeats that placeholder and the obsolete always-rebuild rule.
- Direct-source parser and behavior coverage lives in
  `tests/test_config_parser.py`, `tests/conftest.py`, and
  `tests/fixtures/configuration/signing-cases.toml`. These are supported test
  consumers and must migrate with the parser.
- No additional tracked operator configuration contains `ipa_url`. External
  files outside the repository are not discoverable here, so the parser must
  provide a precise migration diagnostic rather than silently accepting them.

### Backend qualification callers

- The five package tools are `build_backend_qualification_fixture`,
  `qualify_backend_prerequisites`, `exercise_zsign_backend`,
  `exercise_codesign_oracle`, and `compare_backend_qualification`.
- Current workflows do not call those CLIs. PR validation instead exercises
  the real patched backend through pytest with `ZSIGN_BIN`; production uses the
  normal package CLI.
- Repository callers of the tools are their direct tests plus two internal
  imports: the oracle and comparison modules import shared declarations from
  `exercise_zsign_backend`.
- The real patched-backend adapter contract, deterministic fixture, tampered or
  unsigned negative case, and macOS oracle comparison remain supported. The
  standalone prerequisite/mutation CLI and multi-command wrapper shape are
  deletion candidates after one production-primitive-based qualification
  command owns equivalent evidence.

### Documentation and links

- Current supported documents are `README.md`, `docs/operator-runbook.md`,
  `docs/security.md`, and `docs/troubleshooting.md`.
- `IMPROVEMENT_PLAN.md` and `docs/serverless-migration-plan.html` are completed
  historical plans and deletion candidates once any still-current instruction
  is moved into the focused documents.
- `MIGRATION.md` mixes current and completed migrations. Keep only field/CLI
  migration guidance still required by supported contracts. Its only current
  internal reference to the serverless plan must be removed or replaced before
  that historical plan is deleted.
- Archived OpenSpec artifacts and their external evidence links remain the Git
  history/specification record and are not cleanup candidates for this change.

### Agent-client consumers

- `.codex/skills/` is the canonical and supported repository-local OpenSpec
  instruction source, as selected by the change design.
- `.cursor/`, `.kimi/`, and `.pi/` contain hand-maintained copies of the same
  four OpenSpec skills; `.cursor` and `.pi` also add prompt/command wrappers.
- No repository workflow, generator, equality check, or documented supported
  consumer requires those mirrors. They are deletion candidates; no new
  bespoke synchronization mechanism will be added.

## Selected source and toolchain policy

### Measured production IPA assets

The production selection logic was applied to the latest stable release for
all seven configured tasks on 2026-07-23. Each selected asset was then streamed
from its HTTPS download URL; measured bytes and SHA-256 matched GitHub's
advertised evidence.

| Task | Release / selected asset | Measured bytes | SHA-256 |
| --- | --- | ---: | --- |
| JHenTai | `v8.0.14+323` / `JHenTai_8.0.14+323.ipa` | 15,692,732 | `f95896fc5d958bf86f3525e8120f670ce5855e0ce83cdcd5d36853a21c193d10` |
| Eros FE | `v1.9.2+566` / `Eros-FE_1.9.2+566.ipa` | 24,741,532 | `5fcbe9ef39e578116932edec18c1dd8715e91a7e7441cc35167ca5b295e31f39` |
| Asspp | `4.2.1` / `Asspp.ipa` | 15,162,563 | `07119bdc1447406fe8a813ffaf3abc1e735ded83a7c7a14d06ed44cdbc9b6625` |
| PiliPlus | `2.1.0` / `PiliPlus_ios_2.1.0+5109.ipa` | 23,440,601 | `c6ef7e1ebe45351a6a2fafa26180583e53b0e1d9a1c10b8f3663ca83df4363a4` |
| LiveContainer | `3.8.0` / `LiveContainer.ipa` | 4,707,271 | `b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6` |
| Reynard | `0.8.0` / `Reynard.ipa` | 108,266,929 | `48755666a53e0790e5206303a7a68c648f6b603070a9b03dbcfb562aa1de18ac` |
| StikDebug | `3.1.6` / `StikDebug-3.1.6.ipa` | 11,028,710 | `8525e946e40168f5be6b7b5289a6fc973ada79ffe344775643409be52316962f` |

The package-owned source ceiling is **256 MiB (268,435,456 bytes)**. The
largest observed asset is 103.25 MiB, leaving 152.75 MiB or 147.9 percent
headroom. This is intentionally reviewed package policy rather than a task
override; a future larger legitimate IPA requires updating the evidence and
policy instead of bypassing the limit.

### Exact toolchain contracts

- **Python 3.11.15**: current security release in the repository's supported
  3.11 line when selected. The exact interpreter passed the frozen install,
  684-test/95.03-percent coverage suite, Black, isort, and strict mypy over the
  package and scripts.
- **Node.js 22.23.1**: current Node 22 LTS release when selected (bundled npm
  10.9.8). The exact runtime passed `npm ci`, web golden tests, TypeScript, and
  the production Next.js build.
- **uv 0.11.31**: current uv release when selected. It performed the exact
  Python 3.11.15 frozen synchronization and every Python compatibility check.

Compatibility checks retained the original Git blob hashes for `uv.lock` and
`web/package-lock.json`. The Python check used a separate writable temporary
project environment because the pre-existing local `.venv` could not be
replaced on this machine; the candidate toolchain itself completed cleanly.

## Baseline acceptance and rollback boundary

- Migration inputs are explicit: the tracked production file has no direct
  source, the direct-source example/fixtures are enumerated above, the three
  single-choice signing fields occur in the production/example/fixture surface,
  and unknown external configuration will receive field-level migration errors.
- Supported and deletion-candidate qualification, documentation, and agent
  consumers are recorded before any deletion.
- Exact runtime versions and the 256 MiB source policy are selected with live
  release, compatibility, and asset evidence.
- All collection commands were reads, local validation, dependency installation,
  or uncredentialed source downloads. No production CLI stage, Apple or R2
  adapter, signing action, registry revalidation, publication, or credentialed
  mutation ran.
- The repository remained on `master` at `origin/master`; the only Git status
  entry was the untracked OpenSpec change directory supplied for this work.
- Code, workflow, configuration, lock, and documentation changes can be rolled
  back together with a normal Git revert. If rollback occurs after configuration
  migration, the parser/model and both repository TOML files must be restored
  together. Published object and registry schemas are unchanged, so this change
  requires no production data rollback.

The baseline section is accepted with these boundaries.

## Toolchain section acceptance

Accepted on 2026-07-23 with Python 3.11.15, Node.js 22.23.1, npm 10.9.8,
and uv 0.11.31:

- Frozen Python sync passed; 695 tests passed and 3 opt-in tests skipped with
  95.03 percent package coverage. The default run emitted no HTML report.
- Black, isort, strict package mypy, and strict script mypy passed.
- `uv audit --frozen` reported no known Python vulnerabilities.
- Exact-Node `npm ci`, plist golden tests, TypeScript, and the Next.js 16.2.11
  production build passed.
- The npm gate accepted exactly the owned, unexpired
  `GHSA-f88m-g3jw-g9cj` exception and no unreviewed blocking advisory.
- actionlint passed; strict high-severity zizmor reported no finding.
- `git diff --check` passed.
- Pre/post validation SHA-256 values were identical, proving validation did not
  rewrite either lock: `uv.lock`
  `1cf1ef8cdad1c5e39661fef67166bf20a23b473f7be41f9d79c5b1cb04d74474`,
  `web/package-lock.json`
  `5d2bce620861257049279ff1be0a26080fe2d2e671d27c791bfaeaf01bf80aa1`.

## Source intake section acceptance

Accepted on 2026-07-23 with the package-owned 256 MiB policy, a 60-second
per-attempt timeout, 1 MiB chunks, three attempts, and bounded backoff:

- Source URLs and every redirect require HTTPS with a valid authority. Declared
  and streamed byte limits, GitHub advertised size, canonical GitHub SHA-256,
  and workspace non-overwrite behavior are enforced before promotion.
- Each attempt uses a new task-local temporary file. Retryable transport and
  server failures reuse the same selected URL and do not invoke release
  resolution again; exhausted retries retain only a redacted attempt count.
- Canonical source-selection evidence now retains expected and actual size,
  expected and actual SHA-256, and download-attempt count. When GitHub has no
  digest, the measured SHA-256 becomes the run-bound expected digest before
  inventory or any downstream stage.
- Distinct transfer-limit, advertised-size, digest, redirect, and retry-budget
  diagnostics persist on the failed source-stage manifest. Failure-injection
  tests prove inventory, policy evaluation, and Apple planning are not entered.
- 64 focused source/GitHub/state/pipeline tests passed, and strict mypy passed
  over all 103 package and script source files.
- The complete suite passed with 720 tests, 3 opt-in tests skipped, and 95.03
  percent package coverage. Every negative downloader test asserted that no
  promoted or temporary source artifact remained.
- With `SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION=1`, both checksum-pinned
  LiveContainer 3.8.0 assets passed real HTTPS download, expected-size,
  expected-SHA-256, extraction, and inventory checks. An initial transient
  response-size mismatch was rejected without an artifact; a fresh run against
  the same reviewed identities then passed both fixtures.

No production pipeline command, credentialed adapter, signing operation,
publication, or registry mutation was run for this acceptance.

## Immutable direct-source section acceptance

Accepted on 2026-07-23:

- Direct task parsing now requires an HTTPS `ipa_url` and canonical
  64-character `ipa_sha256`. Missing, prefixed, non-hex, or misplaced GitHub
  values fail before network access with field-level diagnostics; legacy tasks
  receive the `shasum -a 256 <path-to-ipa>` migration command.
- The typed source model retains the configured digest. Resolution passes it
  through the bounded downloader and the common expected/actual evidence
  schema, so a direct run cannot inventory bytes that differ from review.
- Production configuration contains no direct source. The example, signing
  fixtures, test configuration, README, migration guide, and project contract
  now use or document HTTPS plus `ipa_sha256`; both repository TOML files parse
  successfully (7 production tasks and 4 example tasks).
- Complete signing fingerprints already retain the actual source SHA-256 and a
  redacted SHA-256 of the source URL. Dedicated tests prove that either identity
  component changes the fingerprint without retaining URL secrets.
- An unchanged direct-source task enters the existing cache candidate path and
  is accepted only after full reopen verification. Force rebuild, first-run or
  schema/fingerprint drift, prerequisite/profile drift, missing or tampered
  signing evidence, and tampered artifacts still rebuild or reject the hit.
- 96 focused parser, fingerprint, cache, source-resolution, fixture, and
  production-pipeline tests passed. The complete suite passed with 729 tests,
  3 opt-in tests skipped, and 95.02 percent package coverage; Black, isort,
  strict mypy over 103 source files, and `git diff --check` passed.

No production side effect was performed for this acceptance.

## Web registry section acceptance

Accepted on 2026-07-23 with Node.js 22.23.1, npm 10.9.8, and Next.js 16.2.11:

- A dependency-free decoder validates the registry root, application array,
  object entries, non-empty identity fields, slug syntax and uniqueness, HTTPS
  IPA URLs, and HTTPS-or-empty icon URLs. Errors expose only the invalid field,
  never unchecked registry values.
- The page and ITMS handler share `getApps()` and therefore receive only decoded
  `AppEntry` objects. First-load transport, HTTP, JSON, and schema failures now
  throw instead of synthesizing an apparently successful empty catalog.
- Origin fetches explicitly use `cache: "force-cache"` plus the `apps` tag.
  Header-authenticated revalidation calls `revalidateTag("apps", "max")`; no
  query-string secret is accepted. The stale-while-revalidate profile leaves a
  prior valid Data Cache entry eligible when a refresh throws, and the loader no
  longer catches that failure into a replacement value.
- `APPS_DATA_MODE` must explicitly select `origin` or `fixture`. A Vercel
  production deployment rejects fixture mode and origin mode requires an HTTPS
  `R2_APPS_JSON_URL`; PR builds explicitly select the validated fixture. README
  and operator environment instructions match the workflow contract.
- Request-level tests prove authorized/unauthorized cache behavior, known and
  unknown slugs, HTTPS manifest data, XML escaping, registry-error propagation,
  XML content type, and `public, max-age=0, must-revalidate` delivery.
- A locked `npm ci` completed, 22 Node tests and both byte-identical plist
  goldens passed, strict TypeScript passed, and the fixture-mode production
  build completed. The audit gate accepted exactly the owned unexpired sharp
  advisory exception. `web/package-lock.json` remained
  `5d2bce620861257049279ff1be0a26080fe2d2e671d27c791bfaeaf01bf80aa1`,
  and `git diff --check` passed.

No Vercel tag, R2 registry, or production deployment state was changed.

## Pre-refactor pipeline evidence inventory

Captured before the manifest/stage extraction on 2026-07-23:

- `inspect`, `plan`, `sync`, `sign`, `verify`, and `publish` each call
  `_inspect_contexts`, which reloads source state and reopens/extracts/inventories
  the unsigned IPA. `sign`, `verify`, and `publish` then call
  `prepare_package_signing`, which inventories the same unsigned IPA again.
- A full publishing `run` therefore performs nine unsigned inventories per task:
  one each in inspect/plan/sync and two each in sign/verify/publish. A full
  non-publishing run performs seven. Only the first source call downloads;
  later calls hash `source.ipa` and reload the unversioned selection document.
- The source surface consists of a read-only task-local `source.ipa` plus an
  unversioned `source-selection.json` containing `url`, `expected_sha256`,
  `advertised_size`, and free-form evidence. The `BundleGraph` has canonical
  schema version 1 serialization but is transient and has no parser/store.
- Ordered stage manifests are canonical schema version 1 documents, but they
  retain only predecessor/result digests rather than the typed source or graph.
  Signing reports and command payloads use schema version 1; run reports use
  schema version 1; signing fingerprints and the cache index use schema version
  2. Cache decisions are currently plain non-atomic JSON without a schema.
- Signed-output verification and cache-hit verification independently reopen
  their candidate IPA and are trust boundaries that must not reuse the unsigned
  graph. Source selection, stage manifests, cache indexes, signing reports, and
  run reports otherwise already use task-scoped or canonical persistence.
- `ProductionPipeline` is 1,101 lines before extraction. Its public commands,
  command payload keys, run-report keys, stage order, and error codes form the
  compatibility baseline for sections 6 and 7.

## Canonical source and inventory manifest section acceptance

Accepted on 2026-07-23:

- Schema-versioned source and inventory documents bind the run ID, task,
  selected URL, expected and actual bytes/digests, download attempts, source
  stage, canonical bundle graph, graph digest, inventory stage, and successful
  predecessor identities. Files are private and promoted with the shared
  fsync-and-replace writer.
- Reload reconstructs typed `ResolvedSource`, `DownloadedSource`, `SourceAsset`,
  and `BundleGraph` values only after canonical schema, identity, document
  digest, source file size/digest, graph digest, and predecessor-chain checks.
- `inspect` is now the only unsigned inventory producer. Repeating `inspect`
  and then running `plan` and `sync` for one unchanged run/task measured one
  unsigned inventory total. `sign`, `verify`, and `publish` load the same typed
  evidence, and package-signing preparation requires that graph explicitly.
- Signed-output and cache-hit verification remain independent trust boundaries:
  they reopen the candidate signed IPA through the full package verifier and do
  not substitute the unsigned-input graph.
- Missing, truncated, cross-run, cross-task, unsupported-schema, file-tampered,
  document/graph-digest-mismatched, and failed-predecessor evidence all fail
  closed. Production-side injection proves representative failures occur before
  Apple, signing, cache-decision, publication, or resource-plan side effects.
- Cache decisions now use canonical atomic persistence. An injected interruption
  before promotion retained the prior complete file and removed the temporary
  candidate.
- The complete suite passed with 757 tests, 3 opt-in tests skipped, and 95.05
  percent package coverage. Black, isort, strict mypy over 104 source files,
  report-schema tests, full stage failure injection, and `git diff --check`
  passed.

No credentialed Apple call, signing backend, cache promotion, publication, or
registry mutation was performed for this acceptance.

## Thin production orchestrator section acceptance

Accepted on 2026-07-23:

- Concrete leaf modules under `pipeline/stages` now own source/inventory,
  ordered evidence, Apple plan/apply, signing/cache, independent verification,
  publication, and stable result/report construction. No stage imports the
  production coordinator.
- `ProductionPipeline` retains the public command functions and compatibility
  helpers but is now a 369-line ordered coordinator, down from the 1,101-line
  pre-refactor baseline. Dependencies are concrete and typed; no service
  container, abstract stage base, or parallel orchestration engine was added.
- Signing cache rejection and ordinary rebuilds call one shared
  execute-copy-signing-report-cache implementation. Cache hits still reopen the
  artifact and restore digest-bound signing evidence before copying it.
- Apple read-only planning, explicit apply gating, aggregated preflight,
  created-resource journaling, SIGTERM cancellation routing, independent signed
  verification, atomic publisher behavior, cache promotion timing, cleanup, and
  report/public keys remain covered by the existing behavioral suites.
- Stage-group acceptance passed independently: source 77 tests, Apple 31,
  sign/cache 52, verification 44, and publication/reporting 52. The full suite
  passed with 759 tests, 3 opt-in tests skipped, and 95.13 percent coverage.
- Strict mypy passed over 113 source files; Black, isort, `git diff --check`,
  cold stage imports, leaf-import assertions, and an explicit DFS import-cycle
  check passed.

No credentialed Apple mutation, real signing, cache publication, registry
mutation, or external deployment was performed for this acceptance.

## Fixed signing invariants section acceptance

Accepted on 2026-07-23:

- `SigningPolicy` no longer exposes identifier-strategy, unknown-bundle, or
  profile-type choices. The parser rejects every former field before decoding
  the rest of the signing table and returns exact field-level removal guidance.
- Production, example, and signing-fixture TOML plus current README examples
  omit all three fields. The migration guide records that suffix preservation,
  uncovered-profile-bundle rejection, and iOS development profiles are fixed.
- Apple resource intents and package profile loading consume
  `IOS_APP_DEVELOPMENT` directly. Identifier derivation and inventory-policy
  reconciliation remain unconditional, so no impossible policy branch remains.
- Golden checks retain the pre-migration policy SHA-256 values for production
  LiveContainer (`ab841751...28237`), production Reynard (`413b9b8d...65d93`),
  and example LiveContainer (`1dc7d2ff...9c66b`). The fingerprint serializer
  permanently retains the fixed values as wire-format invariants without
  exposing them as configuration or domain fields.
- Existing characterization tests retain exact target identifiers, uncovered
  extension failure, App Group mapping, entitlement materialization, profile
  requests, publication flags, and batch publication configuration.
- The two repository configurations parsed as 7 production tasks and 4 example
  tasks. The full suite passed with 759 tests, 3 opt-in tests skipped, and 95.11
  percent coverage; strict mypy passed over 113 source files, and Black, isort,
  and `git diff --check` passed.
- A declaration search found the three removed assignments only in the archived
  2026-07-22 migration design. Active string references are limited to parser
  rejection diagnostics, their tests, migration guidance, and the permanent
  cache fingerprint wire-format compatibility record. The removed enum types
  and `task.signing` field reads have no remaining callers.

No credentialed Apple mutation, real signing, cache publication, registry
mutation, or external deployment was performed for this acceptance.

## Backend qualification implementation evidence

Implemented and locally verified on 2026-07-23, with the Linux runner gate still
open in task 9.7:

- `sideloadedipa-qualify-backend` is the only supported qualification command.
  It writes schema-versioned, digest-bound fixture, backend, plan, output,
  oracle, and comparison evidence without retaining private artifacts or paths.
- Apple preparation calls the production `inspect`, `plan`, and `sync` methods
  in order. The removed prerequisite/reset implementation, its 334-line test,
  and all independent CLI wrappers have no remaining caller; the retained
  fixture/backend/oracle/comparison modules are internal libraries.
- `patches/zsign/qualification-contract.json` binds the reviewed source commit
  and archive digest, patch set, backend version, invocation shape, entitlement
  policy behavior, and supported platforms, so any such change requires review.
- A locally built macOS arm64 patched zsign reported
  `1.1.1+sideloadedipa.3` and SHA-256
  `c4191db9c7ce007f8d9bca0aefb6cd1cc2ee1c0b8458e38ea708fe142c2b1cb8`.
  The real production adapter passed distinct profile/entitlement material,
  helper, signature, and output checks; modifying the signed helper caused the
  independent signature verifier to fail as required.
- Command/report tests cover digest binding, production preparation order,
  missing credentials, a supplied oracle, and absent Linux/macOS oracle
  prerequisites. Missing credentials remain failed evidence; a missing macOS
  identity/keychain/codesign oracle is `manual-gate-unmet` with exit `3`, never
  success. Strict mypy and the one-command repository caller search passed.
- The PR workflow builds the checksum-pinned patched binary on Ubuntu and
  injects it into the mandatory real-backend test. This macOS host has no
  Docker, Podman, Lima, Colima, or OrbStack Linux runtime, so that Linux-specific
  execution has not been claimed locally and task 9.7 remains unchecked until
  the PR runner supplies its platform evidence.

No credentialed Apple preparation, macOS keychain oracle, R2 publication, or
device installation was performed for this evidence.

## Documentation and repository surface section acceptance

Accepted on 2026-07-23:

- `README.md` is now a 115-line overview, safety summary, quick start,
  validation entry point, and focused-document index. Durable task/source/
  signing/publication/environment details moved to `docs/configuration.md`,
  while stage ownership, evidence, trust, cache, publication, and web behavior
  moved to `docs/architecture.md`.
- `MIGRATION.md` now contains only currently actionable direct-digest, removed
  signing-field, package-CLI, header-authenticated revalidation, and backend-
  qualification migrations. Completed serverless and improvement plans were
  deleted after their lasting current behavior was retained in focused docs.
- All 20 unsupported `.cursor`, `.kimi`, and `.pi` instruction/prompt mirrors
  were removed. The four `.codex/skills/*/SKILL.md` files are the only supported
  repository-local agent instructions, matching the recorded consumer decision.
- The focused operational-document and agent-instruction surface measured 31
  files and 5,710 lines immediately before cleanup. It now measures 11 files
  and 1,570 lines: 22 obsolete files were deleted, two focused documents were
  added, and the net surface is 20 files and 4,140 lines smaller.
- Local-link validation passed for all seven current Markdown documents. No
  active document or entry point references the deleted plans or mirror paths.
  The only tracked `?secret=` match outside archived history is the Web negative
  test proving query-string authentication is rejected; supported examples use
  `X-Revalidate-Secret`. The stale, unconsumed `CONFIG_TOML` example was removed.
- Root CLI help, inspect help, and the single backend-qualification help command
  all exited successfully. Both task TOML files parsed (7 production tasks and
  4 example tasks), 70 CLI/config/qualification tests passed without the global
  coverage gate, and `git diff --check` passed.

No production command, credentialed operation, Apple mutation, signing,
publication, R2 update, or Web revalidation was performed for this acceptance.

## Final local acceptance and handoff evidence

Accepted on 2026-07-23 subject only to the explicit Linux PR gate in task 9.7:

- The final Python 3.11.15 run collected 754 tests and passed all 754 with no
  skips and 95.21 percent package coverage. This run enabled both checksum-
  pinned LiveContainer 3.8.0 external fixtures and the real locally built
  patched-zsign contract, including its tampered-output negative assertion.
- Black checked 195 files, isort passed, and strict mypy passed over all 113
  supported package/script source files. The explicit non-publishing `run`
  composition and missing-credential qualification failure also passed.
- `uv audit --frozen` found no known vulnerability or adverse status in 34
  packages. Locked npm validation accepted only the owned, unreachable
  `GHSA-f88m-g3jw-g9cj` sharp exception expiring 2026-08-23; 22 Web tests, both
  byte-identical plist goldens, TypeScript, and the fixture-mode Next.js 16.2.11
  production build passed. The package lock remained
  `5d2bce620861257049279ff1be0a26080fe2d2e671d27c791bfaeaf01bf80aa1`.
- actionlint 1.7.12 and strict high-severity zizmor passed. Every remote Action
  reference is a 40-hex commit with an adjacent version comment, and the only
  workflows remain `pr-checks.yml` and `sign-and-upload.yml`.
- Strict validation passed for this change and for all 13 OpenSpec items.
  Configuration parsing, seven-document local-link validation, stage import
  acyclicity/leaf ownership, CLI help/error behavior, and `git diff --check`
  passed.
- A focused 113-test compatibility pass retained CLI exits/errors, stage/run/
  signing/verification report schemas, public object keys, registry behavior,
  cache re-verification, publication compensation, cancellation, and failure-
  injection side-effect boundaries.
- The final status contains only change-scoped source, tests, locks, workflows,
  configuration, OpenSpec evidence, documentation, and recorded deletions.
  Ninety-four changed/new readable files were scanned with no high-confidence
  private-key/token pattern or file larger than 1 MB. No coverage output,
  dependency tree, Web build, work evidence, or distribution artifact is
  tracked; obsolete wrappers, mirrors, query-secret examples, and historical
  plans have no supported caller.

The independent macOS codesign oracle still requires the operator's temporary
identity and keychain, and OTA behavior still requires device acceptance. The
Linux patched-backend execution is mandatory in PR CI and remains the only
unchecked OpenSpec task locally. No credentialed Apple mutation, cache promotion,
publication, R2 registry update, Web revalidation, commit, or push was performed.
