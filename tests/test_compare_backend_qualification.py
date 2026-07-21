"""Tests for the Linux/macOS signing-backend qualification comparison."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from compare_backend_qualification import ComparisonError, compare_summaries
from exercise_zsign_backend import TARGETS


def qualification_summaries() -> tuple[dict, dict]:
    profiles = {
        role: {
            "embedded_profile_sha256": role * 64,
            "profile_matches_input": True,
        }
        for role in TARGETS
    }
    linux_entitlements = {
        role: {
            "entitlement_keys": ["application-identifier", "keychain-access-groups"],
            "keychain_group_count": 2,
            "keychain_groups_sha256": f"linux-{role}",
        }
        for role in TARGETS
    }
    macos_entitlements = copy.deepcopy(linux_entitlements)
    for role in ("root", "process"):
        macos_entitlements[role]["keychain_group_count"] = 128
        macos_entitlements[role]["keychain_groups_sha256"] = f"macos-{role}"

    codesign_evidence = {
        role: {
            "der_sha256": "a" * 64,
            "der_slot_sha256": "b" * 64,
            "expected_strings_present": True,
            "xml_matches_expected": True,
            "xml_sha256": "c" * 64,
        }
        for role in TARGETS
    }
    linux = {
        "backend": "zsign",
        "contract_pass": False,
        "profiles": profiles,
        "signed_entitlements": linux_entitlements,
        "violations": [
            "root does not contain the exact 128 keychain groups",
            "process does not contain the exact 128 keychain groups",
        ],
    }
    macos = {
        "backend": "codesign",
        "codesign_evidence": codesign_evidence,
        "contract_pass": True,
        "nested_signature_verified": True,
        "profiles": copy.deepcopy(profiles),
        "signed_entitlements": macos_entitlements,
        "signing_order": ["launch", "process", "share", "root"],
        "violations": [],
    }
    return linux, macos


def test_comparison_accepts_reviewed_backend_difference() -> None:
    linux, macos = qualification_summaries()

    result = compare_summaries(linux, macos)

    assert result == {
        "backend_decision_required": True,
        "codesign_contract_pass": True,
        "linux_profile_only_contract_pass": False,
        "profile_mapping_matches": True,
        "roles": ["launch", "process", "root", "share"],
        "root_last": True,
        "xml_der_evidence_complete": True,
    }


def test_comparison_rejects_codesign_without_exact_keychain_contract() -> None:
    linux, macos = qualification_summaries()
    macos["signed_entitlements"]["root"]["keychain_group_count"] = 127

    with pytest.raises(ComparisonError, match="lacks 128"):
        compare_summaries(linux, macos)


def test_comparison_rejects_profile_mapping_difference() -> None:
    linux, macos = qualification_summaries()
    macos["profiles"]["share"]["embedded_profile_sha256"] = "different"

    with pytest.raises(ComparisonError, match="share profile evidence differs"):
        compare_summaries(linux, macos)
