# Development and validation

Read for implementation, refactoring, or local validation. For service boundaries
use [Architecture](architecture.md); credentials are in [Environment](environment.md).

## Sources of truth

- Python/runtime/dependencies/style/coverage: pyproject.toml, uv.lock, .python-version.
- Web scripts/dependencies/runtime: web/package.json, web/package-lock.json, web/.node-version.
- Required CI gates: .github/workflows/pr-checks.yml.

Python uses 3.11-compatible syntax, strict mypy, Black at 100 columns and isort's
Black profile. Domain values are typed and immutable. Use pathlib and argv-list
subprocesses; reuse existing JSON/digest, atomic-write, retry and redaction
primitives. There is no configured Ruff gate.

Tests live in tests/ and web/test/. Test doubles in tests/fakes.py model protocols,
not cryptographic signing. For wiring changes cover production entry/stage
reachability; for security changes include negative cases asserting no forbidden
side effect. Preserve digest and plist golden contracts deliberately.

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
npx tsc --noEmit
APPS_DATA_MODE=fixture npm run build
```

npm test includes plist golden checks. Fixture mode is for local/CI validation;
production Vercel deployments reject it.

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
