"""Contracts for checksum-pinned workflow signing tools."""

from __future__ import annotations

import json
from pathlib import Path

from sideloadedipa.adapters.apple.asc import SUPPORTED_ASC_VERSION

ROOT = Path(__file__).parents[1]
SIGN_WORKFLOW = ROOT / ".github" / "workflows" / "sign-and-upload.yml"
PR_WORKFLOW = ROOT / ".github" / "workflows" / "pr-checks.yml"
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
    assert workflows.count(ASC_SHA256) == 4
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
