"""Tests for the upstream zsign backend qualification exercise."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from exercise_zsign_backend import (
    TARGETS,
    evaluate_contract,
    redacted_output,
    signing_order,
    zsign_command,
)


def entitlement_contract(keychain_groups: list[str]) -> dict[str, dict]:
    common = {
        "com.apple.security.application-groups": ["group.example"],
        "get-task-allow": True,
        "keychain-access-groups": ["TEAM.*"],
    }
    result = {role: dict(common) for role in TARGETS}
    for role in ("root", "process"):
        bundle_identifier = TARGETS[role][2]
        result[role].update(
            {
                "application-identifier": f"TEAM.{bundle_identifier}",
                "com.apple.developer.healthkit": True,
                "com.apple.developer.healthkit.access": ["health-records"],
                "com.apple.developer.healthkit.background-delivery": True,
                "com.apple.developer.kernel.increased-memory-limit": True,
                "keychain-access-groups": keychain_groups,
            }
        )
    return result


def test_zsign_command_uses_four_profiles_without_global_entitlements(tmp_path: Path) -> None:
    command = zsign_command(
        tmp_path / "zsign",
        tmp_path / "private-key.pem",
        tmp_path / "certificate.pem",
        tmp_path / "profiles",
        tmp_path / "fixture.ipa",
        tmp_path / "signed.ipa",
    )

    assert command.count("-m") == 4
    assert "-e" not in command
    assert "-p" not in command


def test_zsign_command_pairs_each_profile_with_its_entitlements(tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    entitlements_dir = tmp_path / "entitlements"

    command = zsign_command(
        tmp_path / "zsign",
        tmp_path / "private-key.pem",
        tmp_path / "certificate.pem",
        profiles_dir,
        tmp_path / "fixture.ipa",
        tmp_path / "signed.ipa",
        entitlements_dir,
    )

    pairs = [command[index : index + 4] for index, value in enumerate(command) if value == "-m"]
    assert pairs == [
        [
            "-m",
            str(profiles_dir / f"{role}.mobileprovision"),
            "-e",
            str(entitlements_dir / f"{role}.plist"),
        ]
        for role in TARGETS
    ]


def test_backend_output_is_bounded_and_redacted() -> None:
    output = f"prefix secret {'x' * 3000}"

    result = redacted_output(output, ["secret"])

    assert "secret" not in result
    assert len(result) == 2000


def test_signing_order_is_parsed_from_backend_evidence() -> None:
    output = """
>>> SignFolder: PlugIns/LaunchAppExtension.appex, (LaunchAppExtension)
>>> SignFolder: PlugIns/LiveProcess.appex, (LiveProcess)
>>> SignFolder: PlugIns/ShareExtension.appex, (ShareExtension)
>>> SignFolder: Qualification.app, (Qualification)
"""

    assert signing_order(output) == ["launch", "process", "share", "root"]


def test_contract_rejects_profile_wildcard_instead_of_128_exact_groups() -> None:
    violations = evaluate_contract(entitlement_contract(["TEAM.*"]))

    assert violations == [
        "root does not contain the exact 128 keychain groups",
        "process does not contain the exact 128 keychain groups",
    ]


def test_contract_accepts_distinct_root_and_extension_entitlements() -> None:
    base = "TEAM.com.kdt.livecontainer.shared"
    keychain_groups = [base, *(f"{base}.{index}" for index in range(1, 128))]

    assert evaluate_contract(entitlement_contract(keychain_groups)) == []
