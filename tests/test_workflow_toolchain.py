"""Contracts for the compact, checksum-pinned CI surface."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from sideloadedipa.adapters.apple.asc import SUPPORTED_ASC_VERSION

ROOT = Path(__file__).parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
ACTION_DIR = ROOT / ".github" / "actions"
SIGN_WORKFLOW = WORKFLOW_DIR / "sign-and-upload.yml"
PR_WORKFLOW = WORKFLOW_DIR / "pr-checks.yml"
SSH_DEBUG_ACTION = ACTION_DIR / "ssh-debug" / "action.yml"
ASC_ACTION = ACTION_DIR / "install-asc" / "action.yml"
ZSIGN_ACTION = ACTION_DIR / "build-patched-zsign" / "action.yml"
ASC_CONTRACT = ROOT / "tests" / "fixtures" / "asc" / "3.1.1-contract.json"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
DEPENDENCY_AUDIT = ROOT / "scripts" / "check_dependency_audits.py"

ASC_SHA256 = "57cca59153eda109faf18d72c8bb0989ed0ee6e0a3082ce73ffa08174afbf2fd"
ZSIGN_SOURCE_COMMIT = "d6e929c97b5b564c2cc1f82afe226a44da7149a0"
ZSIGN_SOURCE_SHA256 = "d9b1577da22a766eabbe1eeb5fc17cc2c4f060e3411a20713f9814fc30f6a670"
PYTHON_VERSION = "3.11.15"
NODE_VERSION = "22.23.1"
UV_VERSION = "0.11.31"


def workflow_text() -> str:
    return SIGN_WORKFLOW.read_text() + PR_WORKFLOW.read_text()


def test_ci_surface_has_only_pr_and_production_workflows() -> None:
    assert {path.name for path in WORKFLOW_DIR.glob("*.yml")} == {
        "pr-checks.yml",
        "sign-and-upload.yml",
    }
    assert {path.parent.name for path in ACTION_DIR.glob("*/action.yml")} == {
        "build-patched-zsign",
        "install-asc",
        "ssh-debug",
    }


def test_workflows_pin_current_canonical_tool_releases() -> None:
    workflows = workflow_text()
    actions = ASC_ACTION.read_text() + ZSIGN_ACTION.read_text()

    assert "github.com/rorkai/App-Store-Connect-CLI/releases/download/" in actions
    assert "codeload.github.com/zhlynn/zsign/tar.gz/" in actions
    assert workflows.count(ASC_SHA256) == 2
    assert workflows.count(ZSIGN_SOURCE_COMMIT) == 2
    assert workflows.count(ZSIGN_SOURCE_SHA256) == 2


def test_local_and_ci_runtime_declarations_are_exact_and_synchronized() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    python_version = (ROOT / ".python-version").read_text().strip()
    node_version = (ROOT / "web" / ".node-version").read_text().strip()

    assert python_version == PYTHON_VERSION
    assert node_version == NODE_VERSION
    assert project["tool"]["uv"]["required-version"] == f"=={UV_VERSION}"

    for workflow in (SIGN_WORKFLOW.read_text(), PR_WORKFLOW.read_text()):
        assert 'version-file: "pyproject.toml"' in workflow
        assert f'python-version: "{PYTHON_VERSION}"' in workflow
        assert f'uv --version | awk \'{{print $2}}\')" = "{UV_VERSION}"' in workflow
        assert "platform.python_version()" in workflow

    pull_request = PR_WORKFLOW.read_text()
    assert 'node-version-file: "web/.node-version"' in pull_request
    assert 'test "$(node --version)" = "v$(tr -d \'\\n\' < .node-version)"' in pull_request


def test_workflows_verify_published_checksum_pin_and_runtime_version() -> None:
    asc_action = ASC_ACTION.read_text()
    zsign_action = ZSIGN_ACTION.read_text()

    assert 'grep -F "  $ASC_ASSET" asc_checksums.txt' in asc_action
    assert 'test "$(asc version | cut -d \' \' -f 1)" = "$ASC_VERSION"' in asc_action
    assert 'test "$("$executable" -v)" = "version: $ZSIGN_EXPECTED_VERSION"' in zsign_action
    for workflow in (SIGN_WORKFLOW.read_text(), PR_WORKFLOW.read_text()):
        assert "uses: ./.github/actions/install-asc" in workflow
        assert "uses: ./.github/actions/build-patched-zsign" in workflow


def test_asc_adapter_contract_matches_workflow_release() -> None:
    contract = json.loads(ASC_CONTRACT.read_text())
    workflows = workflow_text()

    assert SUPPORTED_ASC_VERSION == contract["upstream"]["tag"] == "3.1.1"
    assert contract["upstream"]["repository"] == "rorkai/App-Store-Connect-CLI"
    assert contract["tools"]["linux_amd64_sha256"] == ASC_SHA256
    assert f'ASC_VERSION: "{SUPPORTED_ASC_VERSION}"' in workflows


def test_production_workflow_has_one_job_and_two_manual_inputs() -> None:
    signing = SIGN_WORKFLOW.read_text()
    dispatch = signing.split("  workflow_dispatch:", maxsplit=1)[1].split(
        "  repository_dispatch:", maxsplit=1
    )[0]

    assert re.findall(r"^      ([a-z][a-z0-9_]+):$", dispatch, re.MULTILINE) == [
        "debug",
        "force_rebuild",
    ]
    assert signing.count("\n  sign-and-upload:\n") == 1
    for removed in (
        "dispatch-input-guard",
        "package-shadow",
        "apple-state-probe",
        "backend-qualification",
        "multi_bundle_canary",
        "qualification_apply",
        "qualification_reset_names",
    ):
        assert removed not in signing


def test_cache_is_versioned_and_saved_only_after_successful_signing() -> None:
    signing = SIGN_WORKFLOW.read_text()

    assert "pipeline-cache-v2-${{ runner.os }}" in signing
    assert 'sideloadedipa inspect --run-id "$GITHUB_RUN_ID" --json' in signing
    assert 'sideloadedipa sign --run-id "$GITHUB_RUN_ID" --json' in signing
    assert 'sideloadedipa verify --publish --run-id "$GITHUB_RUN_ID" --json' in signing
    assert 'sideloadedipa publish --run-id "$GITHUB_RUN_ID" --json' in signing
    save_cache = signing.split("- name: Save cache", maxsplit=1)[1].split(
        "- name: Notify webhook", maxsplit=1
    )[0]
    assert "if: ${{ success() }}" in save_cache
    assert "always()" not in save_cache


def test_production_apple_sync_records_plan_and_apply_in_one_cli_transaction() -> None:
    signing = SIGN_WORKFLOW.read_text()

    assert signing.count('sideloadedipa sync --apply --run-id "$GITHUB_RUN_ID"') == 1
    assert "sideloadedipa plan --run-id" not in signing
    assert '.resource_plan | select(type == "object")' in signing
    assert "02-apple-plan.json" in signing
    assert "03-apple-apply.json" in signing


def test_every_backend_dependent_production_step_receives_qualified_identity() -> None:
    signing = SIGN_WORKFLOW.read_text()

    for step_name in (
        "Sign selected tasks",
        "Independently reopen and verify signed IPAs",
        "Publish verified batch",
    ):
        step = signing.split(f"- name: {step_name}", maxsplit=1)[1].split(
            "\n      - name:", maxsplit=1
        )[0]
        assert "ZSIGN_BIN: ${{ steps.patched-zsign.outputs.executable }}" in step
        assert "ZSIGN_SHA256: ${{ steps.patched-zsign.outputs.sha256 }}" in step


def test_production_debug_step_receives_step_scoped_credentials() -> None:
    signing = SIGN_WORKFLOW.read_text()
    production_job = signing.split("  sign-and-upload:", maxsplit=1)[1]
    job_environment = production_job.split("    env:", maxsplit=1)[1].split(
        "    steps:", maxsplit=1
    )[0]
    debug = production_job.split('name: "Debug: Start SSH session"', maxsplit=1)[1]

    assert "secrets." not in job_environment
    for credential in (
        "APPLE_DEV_CERT_P12_ENCODED: ${{ secrets.APPLE_DEV_CERT_P12_ENCODED }}",
        "APPLE_DEV_CERT_PASSWORD: ${{ secrets.APPLE_DEV_CERT_PASSWORD }}",
        "P12_PASSWORD: ${{ secrets.APPLE_DEV_CERT_PASSWORD }}",
        "ASC_KEY_ID: ${{ secrets.ASC_KEY_ID }}",
        "ASC_ISSUER_ID: ${{ secrets.ASC_ISSUER_ID }}",
        "ASC_PRIVATE_KEY_B64: ${{ secrets.ASC_PRIVATE_KEY }}",
        "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
        "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
        "R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}",
        "R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}",
        "R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}",
        "R2_BUCKET: ${{ secrets.R2_BUCKET }}",
        "R2_PUBLIC_BASE_URL: ${{ secrets.R2_PUBLIC_BASE_URL }}",
        "VERCEL_REVALIDATE_SECRET: ${{ secrets.VERCEL_REVALIDATE_SECRET }}",
        "WEBHOOK_URL: ${{ secrets.Instatus_Webhook_URL }}",
    ):
        assert credential in debug
    assert production_job.index("- name: Notify webhook") < production_job.index(
        '- name: "Debug: Start SSH session"'
    )


def test_every_checkout_disables_persisted_repository_credentials() -> None:
    for workflow in (SIGN_WORKFLOW, PR_WORKFLOW):
        text = workflow.read_text()
        assert text.count("uses: actions/checkout@") == text.count("persist-credentials: false")


def test_ssh_debug_session_preserves_caller_environment() -> None:
    action = SSH_DEBUG_ACTION.read_text()
    server = action.split("- name: Start public-key-only SSH server", maxsplit=1)[1].split(
        "    - name:", maxsplit=1
    )[0]

    assert "unset " not in action
    assert '-s -g -e -E -P "$RUNNER_TEMP/dropbear/dropbear.pid"' in server


def test_pr_workflow_uses_one_complete_debuggable_validation_job() -> None:
    pull_request = PR_WORKFLOW.read_text()

    assert "permissions:\n  contents: read" in pull_request
    assert "\n  validation:\n" in pull_request
    assert pull_request.count("\n  validation:\n") == 1
    for removed_job in ("validate-setup", "python-tests", "workflow-validation", "\n  web:\n"):
        assert removed_job not in pull_request
    for command in (
        "uv run pytest",
        "uv run black --check",
        "uv run isort --check-only",
        "uv run mypy src/sideloadedipa",
        "uv run mypy scripts/",
        "actionlint",
        "uv run zizmor --strict-collection --min-severity high .",
        "npm ci",
        "npm run test",
        "npm run build",
    ):
        assert command in pull_request
    assert "ruby -e" not in pull_request
    assert pull_request.count("./.github/actions/ssh-debug") == 1
    assert pull_request.index("npm run build") < pull_request.index(
        'name: "Debug: Start SSH session"'
    )


def test_external_actions_use_immutable_pins_with_version_comments() -> None:
    definitions = [*WORKFLOW_DIR.glob("*.yml"), *ACTION_DIR.glob("*/action.yml")]
    external_uses = []
    for path in definitions:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped.startswith("uses: ") or "uses: ./" in stripped:
                continue
            external_uses.append((path, stripped))

    assert external_uses
    for path, use in external_uses:
        match = re.fullmatch(r"uses: [^@\s]+@([0-9a-f]{40}) # v\d+\.\d+\.\d+", use)
        assert match is not None, f"mutable or undocumented Action pin in {path}: {use}"


def test_dependabot_covers_each_supported_dependency_ecosystem() -> None:
    config = DEPENDABOT.read_text()

    assert config.count("package-ecosystem:") == 3
    assert 'package-ecosystem: uv\n    directory: "/"' in config
    assert 'package-ecosystem: npm\n    directory: "/web"' in config
    assert 'package-ecosystem: github-actions\n    directory: "/"' in config
    assert config.count("interval: weekly") == 3


def test_pr_validation_audits_frozen_locks_with_reviewed_exceptions() -> None:
    pull_request = PR_WORKFLOW.read_text()
    audit_script = DEPENDENCY_AUDIT.read_text()

    assert "uv audit --frozen" in pull_request
    assert "uv run --frozen python scripts/check_dependency_audits.py" in pull_request
    for token in ("npm", "audit", "--package-lock-only", "--audit-level=high", "--json"):
        assert f'"{token}"' in audit_script
    assert "npm audit fix --force" not in pull_request
    assert "npm audit fix --force" not in audit_script


def test_default_coverage_is_terminal_only_and_html_is_explicit() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    addopts = project["tool"]["pytest"]["ini_options"]["addopts"]
    runbook = (ROOT / "docs" / "operator-runbook.md").read_text()

    assert "--cov=sideloadedipa" in addopts
    assert "--cov-report=term-missing" in addopts
    assert "--cov-report=html" not in addopts
    assert project["tool"]["coverage"]["run"]["source"] == ["sideloadedipa"]
    assert project["tool"]["coverage"]["report"]["fail_under"] == 95
    assert "uv run pytest --cov-report=term-missing --cov-report=html" in runbook


def test_actions_aware_validation_replaces_generic_yaml_shape_check() -> None:
    pull_request = PR_WORKFLOW.read_text()
    project = (ROOT / "pyproject.toml").read_text()
    lock = (ROOT / "uv.lock").read_text()

    assert '"zizmor==1.28.0"' in project
    assert 'name = "zizmor"' in lock
    assert "--strict-collection --min-severity high ." in pull_request
    assert "YAML.safe_load_file" not in pull_request
