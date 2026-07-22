"""Contracts for checksum-pinned workflow signing tools."""

from __future__ import annotations

import json
from pathlib import Path

from sideloadedipa.adapters.apple.asc import SUPPORTED_ASC_VERSION

ROOT = Path(__file__).parents[1]
SIGN_WORKFLOW = ROOT / ".github" / "workflows" / "sign-and-upload.yml"
PR_WORKFLOW = ROOT / ".github" / "workflows" / "pr-checks.yml"
SSH_DEBUG_ACTION = ROOT / ".github" / "actions" / "ssh-debug" / "action.yml"
ASC_CONTRACT = ROOT / "tests" / "fixtures" / "asc" / "3.1.1-contract.json"

ASC_SHA256 = "57cca59153eda109faf18d72c8bb0989ed0ee6e0a3082ce73ffa08174afbf2fd"
ASC_MACOS_SHA256 = "47d9be058359ab29c4f562361abfed710b7f24acdaa79c78777bc6e25e118fef"
ZSIGN_SHA256 = "9880b0e1290dea211481fd031bcca8d0d7f3f09ba1c6a89743b3422df1ac14b9"


def test_workflows_pin_current_canonical_tool_releases() -> None:
    workflows = SIGN_WORKFLOW.read_text() + PR_WORKFLOW.read_text()

    assert "rudrankriyam/App-Store-Connect-CLI" not in workflows
    assert 'ASC_VERSION: "2.4.0"' not in workflows
    assert "ZSIGN_VERSION: v1.0.4" not in workflows
    assert "github.com/rorkai/App-Store-Connect-CLI/releases/download/" in workflows
    assert "github.com/zhlynn/zsign/releases/download/" in workflows
    assert workflows.count(ASC_SHA256) == 5
    assert workflows.count(ASC_MACOS_SHA256) == 1
    assert workflows.count(ZSIGN_SHA256) == 3


def test_workflows_verify_published_checksum_pin_and_runtime_version() -> None:
    signing = SIGN_WORKFLOW.read_text()
    pull_request = PR_WORKFLOW.read_text()

    for workflow in (signing, pull_request):
        assert 'grep -F "  $asc_bin" asc_checksums.txt' in workflow
        assert 'test "$(asc version | cut -d \' \' -f 1)" = "$ASC_VERSION"' in workflow
        assert "ASC_LINUX_AMD64_SHA256" in workflow
        assert 'grep -F "  $asset" zsign_SHA256SUMS.txt' in workflow
        assert '"version: ${ZSIGN_VERSION#v}"' in workflow
        assert "ZSIGN_LINUX_MUSL_SHA256" in workflow


def test_asc_adapter_contract_matches_workflow_release() -> None:
    contract = json.loads(ASC_CONTRACT.read_text())
    workflows = SIGN_WORKFLOW.read_text() + PR_WORKFLOW.read_text()

    assert SUPPORTED_ASC_VERSION == contract["upstream"]["tag"] == "3.1.1"
    assert contract["upstream"]["repository"] == "rorkai/App-Store-Connect-CLI"
    assert contract["tools"]["linux_amd64_sha256"] == ASC_SHA256
    assert f'ASC_VERSION: "{SUPPORTED_ASC_VERSION}"' in workflows


def test_cache_is_versioned_and_saved_only_after_successful_signing() -> None:
    signing = SIGN_WORKFLOW.read_text()

    assert "pipeline-cache-v2-${{ runner.os }}" in signing
    assert "ci-cache-v1" not in signing
    production_job = signing.split("  sign-and-upload:", maxsplit=1)[1].split(
        "  package-shadow:", maxsplit=1
    )[0]
    assert "scripts/check_changes.py" not in production_job
    assert "scripts/run_signing.py" not in production_job
    assert 'sideloadedipa inspect --run-id "$GITHUB_RUN_ID" --json' in production_job
    assert 'sideloadedipa sign --run-id "$GITHUB_RUN_ID" --json' in production_job
    assert 'sideloadedipa verify --publish --run-id "$GITHUB_RUN_ID" --json' in production_job
    assert 'sideloadedipa publish --run-id "$GITHUB_RUN_ID" --json' in production_job
    assert "if: ${{ success() && steps.package-publication.outcome == 'success' }}" in signing
    save_cache = signing.split("- name: Save cache", maxsplit=1)[1].split(
        '- name: "Debug:', maxsplit=1
    )[0]
    assert "always()" not in save_cache


def test_production_debug_step_does_not_inherit_job_level_secrets() -> None:
    signing = SIGN_WORKFLOW.read_text()
    production_job = signing.split("  sign-and-upload:", maxsplit=1)[1].split(
        "  package-shadow:", maxsplit=1
    )[0]
    job_environment = production_job.split("    env:", maxsplit=1)[1].split(
        "    steps:", maxsplit=1
    )[0]

    assert "secrets." not in job_environment
    assert 'name: "Debug: Start SSH session"' in production_job
    assert (
        "APPLE_DEV_CERT_P12_ENCODED:"
        not in production_job.split('name: "Debug: Start SSH session"', maxsplit=1)[1].split(
            "- name: Notify webhook", maxsplit=1
        )[0]
    )


def test_ssh_debug_long_lived_processes_drop_production_credentials() -> None:
    action = SSH_DEBUG_ACTION.read_text()

    for step in (
        "Start public-key-only SSH server",
        "Start Cloudflare Tunnel",
        "Hold runner open",
    ):
        body = action.split(f"- name: {step}", maxsplit=1)[1].split("    - name:", maxsplit=1)[0]
        assert "unset APPLE_DEV_CERT_P12_ENCODED APPLE_DEV_CERT_PASSWORD" in body
        assert "unset ASC_KEY_ID ASC_ISSUER_ID ASC_PRIVATE_KEY_B64" in body
        assert "unset GITHUB_TOKEN GH_TOKEN" in body
        assert "unset R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY" in body
        assert "unset VERCEL_REVALIDATE_SECRET WEBHOOK_URL" in body


def test_shadow_and_canary_are_non_publishing_and_retain_only_reports() -> None:
    signing = SIGN_WORKFLOW.read_text()
    shadow = signing.split("  package-shadow:", maxsplit=1)[1].split(
        "  apple-state-probe:", maxsplit=1
    )[0]
    canary = signing.split("  backend-qualification:", maxsplit=1)[1].split(
        "  backend-qualification-macos:", maxsplit=1
    )[0]

    assert "sideloadedipa inspect --json" in shadow
    assert "sideloadedipa plan --json" in shadow
    assert 'publication: "disabled"' in shadow
    assert "retention-days: 3" in shadow
    assert "R2_ACCESS_KEY_ID:" not in shadow
    assert "Publication - enforce disabled canary state" in canary
    assert "R2_ACCESS_KEY_ID:" not in canary
    assert "publication-disabled.json" in canary
    assert "retention-days: 1" in canary


def test_pr_workflow_is_fork_safe_and_exercises_file_manifests() -> None:
    pull_request = PR_WORKFLOW.read_text()

    assert "permissions:\n  contents: read" in pull_request
    assert "secrets.ASC_" not in pull_request
    assert "secrets.APPLE_DEV_CERT" not in pull_request
    assert "run_workflow_fixture.py" in pull_request
    assert "actionlint" in pull_request
    assert pull_request.count("./.github/actions/ssh-debug") == 2


def test_workflows_pin_current_stable_action_releases() -> None:
    workflows = SIGN_WORKFLOW.read_text() + PR_WORKFLOW.read_text()

    assert "astral-sh/setup-uv@v8" not in workflows
    assert "astral-sh/setup-uv@v9.0.0" in workflows
    assert "actions/setup-node@v7.0.0" in workflows
    assert "actions/cache/restore@v6.1.0" in workflows
    assert "actions/cache/save@v6.1.0" in workflows
