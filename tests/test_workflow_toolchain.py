"""Contracts for checksum-pinned workflow signing tools."""

from __future__ import annotations

import json
from pathlib import Path

from sideloadedipa.adapters.apple.asc import SUPPORTED_ASC_VERSION

ROOT = Path(__file__).parents[1]
SIGN_WORKFLOW = ROOT / ".github" / "workflows" / "sign-and-upload.yml"
PR_WORKFLOW = ROOT / ".github" / "workflows" / "pr-checks.yml"
SSH_DEBUG_ACTION = ROOT / ".github" / "actions" / "ssh-debug" / "action.yml"
ASC_ACTION = ROOT / ".github" / "actions" / "install-asc" / "action.yml"
ZSIGN_ACTION = ROOT / ".github" / "actions" / "build-patched-zsign" / "action.yml"
ASC_CONTRACT = ROOT / "tests" / "fixtures" / "asc" / "3.1.1-contract.json"

ASC_SHA256 = "57cca59153eda109faf18d72c8bb0989ed0ee6e0a3082ce73ffa08174afbf2fd"
ASC_MACOS_SHA256 = "47d9be058359ab29c4f562361abfed710b7f24acdaa79c78777bc6e25e118fef"
ZSIGN_SHA256 = "9880b0e1290dea211481fd031bcca8d0d7f3f09ba1c6a89743b3422df1ac14b9"
ZSIGN_SOURCE_COMMIT = "d6e929c97b5b564c2cc1f82afe226a44da7149a0"
ZSIGN_SOURCE_SHA256 = "d9b1577da22a766eabbe1eeb5fc17cc2c4f060e3411a20713f9814fc30f6a670"


def test_workflows_pin_current_canonical_tool_releases() -> None:
    workflows = SIGN_WORKFLOW.read_text() + PR_WORKFLOW.read_text()
    actions = ASC_ACTION.read_text() + ZSIGN_ACTION.read_text()

    assert "github.com/rorkai/App-Store-Connect-CLI/releases/download/" in actions
    assert "github.com/zhlynn/zsign/releases/download/" in workflows
    assert workflows.count(ASC_SHA256) == 5
    assert workflows.count(ASC_MACOS_SHA256) == 1
    assert workflows.count(ZSIGN_SHA256) == 1
    assert workflows.count(ZSIGN_SOURCE_COMMIT) == 3
    assert workflows.count(ZSIGN_SOURCE_SHA256) == 3


def test_workflows_verify_published_checksum_pin_and_runtime_version() -> None:
    signing = SIGN_WORKFLOW.read_text()
    pull_request = PR_WORKFLOW.read_text()

    asc_action = ASC_ACTION.read_text()
    zsign_action = ZSIGN_ACTION.read_text()
    assert 'grep -F "  $ASC_ASSET" asc_checksums.txt' in asc_action
    assert 'test "$(asc version | cut -d \' \' -f 1)" = "$ASC_VERSION"' in asc_action
    assert 'test "$("$executable" -v)" = "version: $ZSIGN_EXPECTED_VERSION"' in zsign_action
    for workflow in (signing, pull_request):
        assert "uses: ./.github/actions/install-asc" in workflow
        assert "uses: ./.github/actions/build-patched-zsign" in workflow
        assert "ZSIGN_SOURCE_COMMIT" in workflow
        assert "ZSIGN_SOURCE_SHA256" in workflow


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
    assert "if: ${{ success() }}" in signing
    save_cache = signing.split("- name: Save cache", maxsplit=1)[1].split(
        "- name: Notify webhook", maxsplit=1
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
    debug = production_job.split('name: "Debug: Start SSH session"', maxsplit=1)[1]
    assert "APPLE_DEV_CERT_P12_ENCODED:" not in debug
    assert production_job.index("- name: Notify webhook") < production_job.index(
        '- name: "Debug: Start SSH session"'
    )


def test_dispatch_mode_inputs_cannot_fall_through_to_production() -> None:
    signing = SIGN_WORKFLOW.read_text()
    production_condition = signing.split("  sign-and-upload:", maxsplit=1)[1].split(
        "    runs-on:", maxsplit=1
    )[0]
    guard = signing.split("  dispatch-input-guard:", maxsplit=1)[1].split(
        "  sign-and-upload:", maxsplit=1
    )[0]

    for option in ("qualification_apply", "qualification_reset_names"):
        assert f"inputs.{option} != true" in production_condition
        assert f"inputs.{option} == true" in guard
    assert "inputs.backend_qualification != true" in guard
    assert "secrets." not in guard


def test_every_checkout_disables_persisted_repository_credentials() -> None:
    for workflow in (SIGN_WORKFLOW, PR_WORKFLOW):
        text = workflow.read_text()
        assert text.count("uses: actions/checkout@") == text.count("persist-credentials: false")


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
    assert "sideloadedipa run --task LiveContainer --apply" in canary
    assert "sideloadedipa sync --task LiveContainer" not in canary
    assert "sideloadedipa sign --task LiveContainer" not in canary
    assert ".[0].verification.passed == true and .[0].publication == null" in canary
    assert "retention-days: 1" in canary
    assert "livecontainer-device-canary" not in canary
    assert "work/signed/LiveContainer.ipa" not in canary


def test_non_production_jobs_scope_secrets_to_consuming_steps() -> None:
    signing = SIGN_WORKFLOW.read_text()
    boundaries = (
        ("package-shadow", "apple-state-probe"),
        ("apple-state-probe", "backend-qualification"),
        ("backend-qualification", "backend-qualification-macos"),
        ("backend-qualification-macos", "backend-qualification-comparison"),
    )

    for job, following_job in boundaries:
        body = signing.split(f"  {job}:", maxsplit=1)[1].split(f"  {following_job}:", maxsplit=1)[0]
        job_environment = body.split("    env:", maxsplit=1)[1].split("    steps:", maxsplit=1)[0]
        assert "secrets." not in job_environment

    canary = signing.split("  backend-qualification:", maxsplit=1)[1].split(
        "  backend-qualification-macos:", maxsplit=1
    )[0]
    cleanup_position = canary.index("- name: Remove private qualification material")
    debug_position = canary.index('- name: "Debug: Start SSH session"')
    assert cleanup_position < debug_position
    debug = canary[debug_position:]
    assert "APPLE_DEV_CERT_P12_ENCODED:" not in debug
    assert "ASC_PRIVATE_KEY_B64:" not in debug


def test_pr_workflow_is_fork_safe_and_validates_workflow_contracts() -> None:
    pull_request = PR_WORKFLOW.read_text()

    assert "permissions:\n  contents: read" in pull_request
    assert "secrets.ASC_" not in pull_request
    assert "secrets.APPLE_DEV_CERT" not in pull_request
    assert "run_workflow_fixture.py" not in pull_request
    assert "actionlint" in pull_request
    assert 'Dir[".github/actions/**/action.yml"]' in pull_request
    assert "uv run mypy scripts/" in pull_request
    assert "continue-on-error: true\n        run: uv run mypy scripts/" not in pull_request
    assert pull_request.count("./.github/actions/ssh-debug") == 2


def test_workflows_pin_current_stable_action_releases() -> None:
    workflows = SIGN_WORKFLOW.read_text() + PR_WORKFLOW.read_text()

    assert "astral-sh/setup-uv@v8" not in workflows
    assert "astral-sh/setup-uv@v9.0.0" in workflows
    assert "actions/setup-node@v7.0.0" in workflows
    assert "actions/cache/restore@v6.1.0" in workflows
    assert "actions/cache/save@v6.1.0" in workflows
