# Development and validation

Read for implementation, refactoring, or local validation. For service boundaries
use [Architecture](architecture.md); credentials are in [Environment](environment.md).

## Sources of truth

- Python/runtime/dependencies/style/coverage: pyproject.toml, uv.lock, .python-version.
- Web scripts/dependencies/runtime: web/package.json, web/package-lock.json, web/.node-version.
- Required CI gates: .github/workflows/pr-checks.yml (static checks, audits, builds)
  and .github/workflows/tests.yml (every test suite).

Python uses 3.14-compatible syntax, strict mypy, Black at 100 columns and isort's
Black profile. Domain values are typed and immutable. Use pathlib and argv-list
subprocesses; reuse existing JSON/digest, atomic-write, retry and redaction
primitives. There is no configured Ruff gate.

Unit tests live in tests/ and web/test/; end-to-end tests live in e2e/ and
web/e2e/. Test doubles in tests/fakes.py model protocols, not cryptographic
signing. For wiring changes cover production entry/stage reachability; for
security changes include negative cases asserting no forbidden side effect.
Preserve digest and plist golden contracts deliberately.

A test asserts behaviour of code. Do not add tests that restate repository
content: production task values in configs/tasks.toml, workflow or pyproject
text, documentation, or fixture literals. Both configuration files are only
checked to pass production validation; whether a task behaves correctly is an
end-to-end question. Take sample tasks and entitlement templates from the
test-owned project in tests/fixtures/project (tests/sample_project.py), never
from configs/, so editing the production task list cannot change a unit result.

## Choose validation by affected boundary

Local fixture tests may run without approval. Optional integration tests can fetch
external IPAs or need a real patched zsign; inspect prerequisites before opting in.
A full pytest run enforces the configured 95% coverage gate. A focused run can disable
coverage to avoid treating partial coverage as a project-wide failure:

```bash
uv sync --frozen
uv run --frozen pytest --no-cov tests/test_config_parser.py
```

For Python changes, the full local gates are:

```bash
uv run --frozen pytest
ZSIGN_BIN=/path/to/patched/zsign uv run --frozen pytest e2e --no-cov
uv run --frozen black --check src/sideloadedipa scripts
uv run --frozen isort --check-only src/sideloadedipa scripts
uv run --frozen mypy src/sideloadedipa scripts
uv build
```

Coverage reports default to terminal output only. Generate a local HTML report
explicitly when needed (generated `htmlcov/` stays out of commits):

```bash
uv run --frozen pytest --cov-report=term-missing --cov-report=html
```

For web changes (from web/):

```bash
npm ci
npm test
npm run test:e2e
npx tsc --noEmit
APPS_DATA_MODE=fixture npm run build
```

npm test includes plist golden checks. Fixture mode is for local/CI validation;
production Vercel deployments reject it.

## End-to-end suites

e2e/ runs the installed `sideloadedipa` command in a child process against a
scratch workspace: HTTPS source intake, inventory, Apple reconciliation, patched
zsign and independent verification are the production code paths. Only what a
test cannot reach is replaced, at a process or network boundary: Apple by a
stand-in `asc` executable on PATH (e2e/fake_asc.py) that issues CMS-signed
profiles, and the IPA host by a local HTTPS server trusted through
`SSL_CERT_FILE`. Signing scenarios fail rather than skip when `ZSIGN_BIN` is
unset; build the binary with .github/actions/build-patched-zsign's steps. The
suite is outside the default pytest collection and the coverage gate.

web/e2e/ builds the site in origin mode and serves it with `next start`
against a local HTTPS registry origin trusted through `NODE_EXTRA_CA_CERTS`,
covering the page, the ITMS manifest, the IPA download it points to, and
secret-gated revalidation.

Neither suite reaches GitHub release resolution or R2 publication: both
endpoints are fixed in production code and have no test transport. Those stages
keep their adapter-level tests in tests/.

For dependency/workflow changes, use the existing audit gates:

```bash
uv lock --check
uv audit --frozen
uv run --frozen python scripts/check_dependency_audits.py
uv run --frozen zizmor --strict-collection --min-severity high .
actionlint -no-color .github/workflows/*.yml
```

Use the workflow's checksum-verified actionlint installation rather than an
unreviewed download. Format changed tests too when needed; CI's formatter targets
are production package and scripts. Documentation-only work needs link/command
consistency checks and git diff --check, not real signing or a full product rebuild.

Do not lower coverage, typing, verification, or vulnerability thresholds to make a
change pass. Describe unavailable tools, skips and failures separately from passed
checks. [Operations](operator-runbook.md) covers real backend qualification.
