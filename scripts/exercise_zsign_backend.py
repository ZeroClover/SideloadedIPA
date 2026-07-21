#!/usr/bin/env python3
"""Exercise upstream zsign's multi-profile behavior against the qualification IPA."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

TARGETS = {
    "root": (
        "Payload/Qualification.app",
        "Qualification",
        "io.zeroclover.app.livecontainer",
    ),
    "process": (
        "Payload/Qualification.app/PlugIns/LiveProcess.appex",
        "LiveProcess",
        "io.zeroclover.app.livecontainer.LiveProcess",
    ),
    "launch": (
        "Payload/Qualification.app/PlugIns/LaunchAppExtension.appex",
        "LaunchAppExtension",
        "io.zeroclover.app.livecontainer.LaunchAppExtension",
    ),
    "share": (
        "Payload/Qualification.app/PlugIns/ShareExtension.appex",
        "ShareExtension",
        "io.zeroclover.app.livecontainer.ShareExtension",
    ),
}
ROOT_ONLY_KEYS = {
    "com.apple.developer.healthkit",
    "com.apple.developer.healthkit.access",
    "com.apple.developer.healthkit.background-delivery",
    "com.apple.developer.kernel.increased-memory-limit",
}


class BackendExerciseError(RuntimeError):
    """The backend exercise could not produce trustworthy evidence."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def zsign_command(
    zsign: Path,
    p12: Path,
    password: str,
    profiles_dir: Path,
    fixture_ipa: Path,
    signed_ipa: Path,
) -> list[str]:
    command = [str(zsign), "-f", "-k", str(p12), "-p", password]
    for role in TARGETS:
        command.extend(["-m", str(profiles_dir / f"{role}.mobileprovision")])
    command.extend(["-o", str(signed_ipa), str(fixture_ipa)])
    return command


def redacted_output(value: str, redactions: Sequence[str]) -> str:
    for secret in redactions:
        if secret:
            value = value.replace(secret, "***")
    return value[-2000:].strip()


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    redactions: Sequence[str] = (),
) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        stdout = redacted_output(result.stdout, redactions)
        stderr = redacted_output(result.stderr, redactions)
        raise BackendExerciseError(
            f"backend command {Path(command[0]).name!r} failed with exit code "
            f"{result.returncode}; stdout={stdout!r}; stderr={stderr!r}"
        )


def inspect_entitlements(zsign: Path, executable: Path, debug_dir: Path) -> dict[str, Any]:
    debug_dir.mkdir(parents=True)
    run([str(zsign), "-d", str(executable)], cwd=debug_dir)
    plist_path = debug_dir / ".zsign_debug" / "Entitlements.plist"
    if not plist_path.is_file():
        raise BackendExerciseError(f"zsign emitted no entitlement evidence for {executable.name}")
    value = plistlib.loads(plist_path.read_bytes())
    if not isinstance(value, dict):
        raise BackendExerciseError(
            f"zsign entitlement evidence is not a dictionary for {executable.name}"
        )
    return value


def expected_keychain_groups(application_identifier: str, bundle_identifier: str) -> set[str]:
    suffix = f".{bundle_identifier}"
    if not application_identifier.endswith(suffix):
        return set()
    prefix = application_identifier[: -len(bundle_identifier)]
    base = f"{prefix}com.kdt.livecontainer.shared"
    return {base, *(f"{base}.{index}" for index in range(1, 128))}


def evaluate_contract(entitlements: Mapping[str, Mapping[str, Any]]) -> list[str]:
    violations: list[str] = []
    app_groups_by_role: dict[str, set[str]] = {}
    for role, (_, _, bundle_identifier) in TARGETS.items():
        values = entitlements[role]
        groups = values.get("com.apple.security.application-groups")
        app_groups_by_role[role] = set(groups) if isinstance(groups, list) else set()
        if not app_groups_by_role[role]:
            violations.append(f"{role} has no App Group entitlement")

        if role in {"root", "process"}:
            missing = sorted(ROOT_ONLY_KEYS - values.keys())
            if missing:
                violations.append(f"{role} is missing root-only keys: {missing}")
            application_identifier = values.get("application-identifier")
            actual_keychain = values.get("keychain-access-groups")
            expected_keychain = (
                expected_keychain_groups(application_identifier, bundle_identifier)
                if isinstance(application_identifier, str)
                else set()
            )
            if not isinstance(actual_keychain, list) or set(actual_keychain) != expected_keychain:
                violations.append(f"{role} does not contain the exact 128 keychain groups")
        else:
            inherited = sorted(ROOT_ONLY_KEYS & values.keys())
            if inherited:
                violations.append(f"{role} inherited root-only keys: {inherited}")

    common_groups = set.intersection(*app_groups_by_role.values())
    if not common_groups:
        violations.append("signed bundles have no common App Group")
    return violations


def exercise(args: argparse.Namespace) -> dict[str, Any]:
    password = os.environ.get(args.p12_password_env)
    if not password:
        raise BackendExerciseError(f"{args.p12_password_env} is not set")

    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    signed_ipa = args.output_dir / "signed.ipa"
    command = zsign_command(
        args.zsign,
        args.p12,
        password,
        args.profiles_dir,
        args.fixture_ipa,
        signed_ipa,
    )
    if "-e" in command or command.count("-m") != len(TARGETS):
        raise BackendExerciseError("qualification command is not repeated-profile/profile-only")
    run(command, redactions=(password,))

    extracted = args.output_dir / "extracted"
    with zipfile.ZipFile(signed_ipa) as archive:
        archive.extractall(extracted)

    entitlements: dict[str, dict[str, Any]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for role, (bundle_path, executable, _) in TARGETS.items():
        bundle = extracted / bundle_path
        signed_profile = (bundle / "embedded.mobileprovision").read_bytes()
        expected_profile = (args.profiles_dir / f"{role}.mobileprovision").read_bytes()
        profile_matches = signed_profile == expected_profile
        profiles[role] = {
            "embedded_profile_sha256": sha256_bytes(signed_profile),
            "profile_matches_input": profile_matches,
        }
        if not profile_matches:
            raise BackendExerciseError(f"{role} embedded profile does not match its input")
        entitlements[role] = inspect_entitlements(
            args.zsign,
            bundle / executable,
            args.output_dir / "debug" / role,
        )

    violations = evaluate_contract(entitlements)
    return {
        "backend": "zsign",
        "command_shape": {"global_entitlements": False, "profile_count": command.count("-m")},
        "contract_pass": not violations,
        "profiles": profiles,
        "signed_entitlements": {
            role: {
                "app_groups": values.get("com.apple.security.application-groups", []),
                "entitlement_keys": sorted(values),
                "healthkit_access": values.get("com.apple.developer.healthkit.access", []),
                "healthkit_background_delivery": values.get(
                    "com.apple.developer.healthkit.background-delivery", False
                ),
                "increased_memory_limit": values.get(
                    "com.apple.developer.kernel.increased-memory-limit", False
                ),
                "keychain_group_count": len(values.get("keychain-access-groups", [])),
                "keychain_groups_sha256": sha256_bytes(
                    json.dumps(
                        sorted(values.get("keychain-access-groups", [])), separators=(",", ":")
                    ).encode()
                ),
            }
            for role, values in entitlements.items()
        },
        "signed_ipa_sha256": sha256_bytes(signed_ipa.read_bytes()),
        "violations": violations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zsign", type=Path, required=True)
    parser.add_argument("--fixture-ipa", type=Path, required=True)
    parser.add_argument("--p12", type=Path, required=True)
    parser.add_argument("--p12-password-env", required=True)
    parser.add_argument("--profiles-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    try:
        summary = exercise(parse_args())
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
        if not summary["contract_pass"]:
            print(
                "[backend-exercise-error] upstream zsign failed the per-bundle contract",
                file=sys.stderr,
            )
            return 2
        return 0
    except (BackendExerciseError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"[backend-exercise-error] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
