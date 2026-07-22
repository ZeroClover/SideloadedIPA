#!/usr/bin/env python3
"""Compare redacted Linux zsign and macOS codesign qualification summaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, cast

from sideloadedipa.tools.exercise_zsign_backend import TARGETS

EXPECTED_LINUX_VIOLATIONS = {
    "root does not contain the exact 128 keychain groups",
    "process does not contain the exact 128 keychain groups",
    *(
        f"{role} embedded profile is not covered by its SHA-256 resource seal"
        for role in TARGETS
        if role != "root"
    ),
}


class ComparisonError(RuntimeError):
    """The two backend qualification results do not satisfy the gate."""


def load_summary(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ComparisonError(f"summary {path.name} is not an object")
    return value


def assert_negative_control(summary: Mapping[str, Any]) -> None:
    if summary.get("backend") != "zsign":
        raise ComparisonError("negative control is not from zsign")
    if summary.get("backend_variant", "upstream-profile-only") != "upstream-profile-only":
        raise ComparisonError("negative control did not use upstream profile-only zsign")
    if summary.get("contract_pass") is not False:
        raise ComparisonError("negative control unexpectedly satisfied the contract")
    if set(summary.get("violations", [])) != EXPECTED_LINUX_VIOLATIONS:
        raise ComparisonError("negative-control violations differ from the reviewed result")


def compare_summaries(
    linux: Mapping[str, Any],
    macos: Mapping[str, Any],
    negative_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if negative_control is not None:
        assert_negative_control(negative_control)
    if linux.get("backend") != "zsign":
        raise ComparisonError("Linux summary is not from zsign")
    if macos.get("backend") != "codesign":
        raise ComparisonError("macOS summary is not from codesign")
    linux_variant = linux.get("backend_variant", "upstream-profile-only")
    extension_selected = linux_variant == "per-profile-entitlements-extension"
    if extension_selected:
        if linux.get("contract_pass") is not True or linux.get("violations") != []:
            raise ComparisonError("Linux per-profile extension did not satisfy the contract")
        command_shape = linux.get("command_shape")
        if command_shape != {
            "entitlement_count": len(TARGETS),
            "global_entitlements": False,
            "profile_count": len(TARGETS),
        }:
            raise ComparisonError("Linux per-profile extension command shape is invalid")
        linux_order = linux.get("signing_order")
        if (
            not isinstance(linux_order, list)
            or set(linux_order) != set(TARGETS)
            or linux_order[-1] != "root"
        ):
            raise ComparisonError("Linux per-profile extension signing order is invalid")
        executable_sha256 = linux.get("executable_sha256")
        if not isinstance(executable_sha256, str) or len(executable_sha256) != 64:
            raise ComparisonError("Linux per-profile extension executable hash is invalid")
        if linux.get("mismatched_entitlement_count_rejected") is not True:
            raise ComparisonError("Linux per-profile extension did not reject a count mismatch")
    else:
        if linux_variant != "upstream-profile-only":
            raise ComparisonError("Linux summary has an unknown backend variant")
        if linux.get("contract_pass") is not False:
            raise ComparisonError("Linux profile-only result did not record the expected failure")
        if set(linux.get("violations", [])) != EXPECTED_LINUX_VIOLATIONS:
            raise ComparisonError("Linux violations differ from the reviewed qualification result")
    if macos.get("contract_pass") is not True or macos.get("violations") != []:
        raise ComparisonError("macOS oracle did not satisfy the exact entitlement contract")
    if macos.get("nested_signature_verified") is not True:
        raise ComparisonError("macOS oracle did not verify nested signatures")

    order = macos.get("signing_order")
    if not isinstance(order, list) or set(order) != set(TARGETS) or order[-1] != "root":
        raise ComparisonError("macOS signing order is not complete and root-last")

    linux_profiles = linux.get("profiles")
    macos_profiles = macos.get("profiles")
    linux_entitlements = linux.get("signed_entitlements")
    macos_entitlements = macos.get("signed_entitlements")
    codesign_evidence = macos.get("codesign_evidence")
    if not all(
        isinstance(value, dict)
        for value in (
            linux_profiles,
            macos_profiles,
            linux_entitlements,
            macos_entitlements,
            codesign_evidence,
        )
    ):
        raise ComparisonError("qualification summaries are missing per-bundle evidence")
    linux_profiles = cast(dict[str, Any], linux_profiles)
    macos_profiles = cast(dict[str, Any], macos_profiles)
    linux_entitlements = cast(dict[str, Any], linux_entitlements)
    macos_entitlements = cast(dict[str, Any], macos_entitlements)
    codesign_evidence = cast(dict[str, Any], codesign_evidence)

    for role in TARGETS:
        if linux_profiles.get(role) != macos_profiles.get(role):
            raise ComparisonError(f"{role} profile evidence differs between runners")
        if macos_profiles[role].get("profile_resource_seal_matches") is not True:
            raise ComparisonError(f"{role} profile resource seal is invalid")
        evidence = codesign_evidence.get(role)
        if not isinstance(evidence, dict):
            raise ComparisonError(f"{role} has no codesign XML/DER evidence")
        if evidence.get("xml_matches_expected") is not True:
            raise ComparisonError(f"{role} XML entitlements differ from the oracle policy")
        if evidence.get("expected_strings_present") is not True:
            raise ComparisonError(f"{role} DER entitlement view is incomplete")
        for field in ("xml_sha256", "der_sha256", "der_slot_sha256"):
            value = evidence.get(field)
            if not isinstance(value, str) or len(value) != 64:
                raise ComparisonError(f"{role} has invalid {field} evidence")

    if extension_selected:
        for role in TARGETS:
            if linux_entitlements[role] != macos_entitlements[role]:
                raise ComparisonError(f"{role} Linux/macOS entitlement evidence differs")
    else:
        for role in ("root", "process"):
            if linux_entitlements[role].get("keychain_group_count") != 2:
                raise ComparisonError(
                    f"{role} Linux keychain count changed from the reviewed result"
                )
            if macos_entitlements[role].get("keychain_group_count") != 128:
                raise ComparisonError(f"{role} macOS oracle lacks 128 keychain groups")
            if linux_entitlements[role].get("keychain_groups_sha256") == macos_entitlements[
                role
            ].get("keychain_groups_sha256"):
                raise ComparisonError(f"{role} Linux/macOS keychain evidence unexpectedly matches")

        for role in ("launch", "share"):
            if linux_entitlements[role] != macos_entitlements[role]:
                raise ComparisonError(f"{role} Linux/macOS entitlement evidence differs")

    return {
        "backend_decision_required": not extension_selected,
        "codesign_contract_pass": True,
        "linux_backend_variant": linux_variant,
        "linux_contract_pass": extension_selected,
        "negative_control_pass": negative_control is not None,
        "profile_mapping_matches": True,
        "roles": sorted(TARGETS),
        "root_last": True,
        "xml_der_evidence_complete": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--linux-summary", type=Path, required=True)
    parser.add_argument("--macos-summary", type=Path, required=True)
    parser.add_argument("--negative-control-summary", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        result = compare_summaries(
            load_summary(args.linux_summary),
            load_summary(args.macos_summary),
            load_summary(args.negative_control_summary),
        )
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ComparisonError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[backend-comparison-error] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
