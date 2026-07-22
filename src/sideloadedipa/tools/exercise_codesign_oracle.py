#!/usr/bin/env python3
"""Produce independent macOS codesign evidence for the backend qualification fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from sideloadedipa.tools.exercise_zsign_backend import (
    TARGETS,
    BackendExerciseError,
    entitlement_evidence,
    evaluate_contract,
    materialize_entitlements,
    profile_resource_seal_matches,
    sha256_bytes,
)


class CodesignOracleError(RuntimeError):
    """The macOS oracle could not produce trustworthy evidence."""


def run(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr)[-2000:].decode(errors="replace")
        raise CodesignOracleError(
            f"{Path(command[0]).name} failed with exit code {result.returncode}: {output}"
        )
    return result


def decode_profile(profile_path: Path) -> dict[str, Any]:
    value = plistlib.loads(run(["security", "cms", "-D", "-i", str(profile_path)]).stdout)
    if not isinstance(value, dict):
        raise CodesignOracleError(f"profile {profile_path.stem} is not a dictionary")
    return value


def signing_order() -> list[str]:
    nested = sorted(
        (role for role in TARGETS if role != "root"),
        key=lambda role: TARGETS[role][0],
    )
    return [*nested, "root"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in string_values(child)]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in [str(key), *string_values(child)]]
    return []


def inspect_codesign_entitlements(bundle: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    xml = run(["codesign", "--display", "--entitlements", "-", "--xml", str(bundle)]).stdout
    actual = plistlib.loads(xml)
    if actual != expected:
        raise CodesignOracleError(f"XML entitlements differ for {bundle.name}")

    der = run(["codesign", "--display", "--entitlements", "-", "--der", str(bundle)]).stdout
    if not der:
        raise CodesignOracleError(f"codesign emitted no DER entitlements for {bundle.name}")

    abstract = run(["codesign", "--display", "--entitlements", "-", str(bundle)]).stdout
    missing = [value for value in string_values(expected) if value.encode() not in abstract]
    if missing:
        raise CodesignOracleError(
            f"DER entitlement view is missing {len(missing)} expected strings for {bundle.name}"
        )

    details = run(["codesign", "--display", "--verbose=5", str(bundle)]).stderr.decode(
        errors="replace"
    )
    slot = re.search(r"^\s*-7=([0-9a-fA-F]{64})$", details, re.MULTILINE)
    if slot is None or set(slot.group(1)) == {"0"}:
        raise CodesignOracleError(f"codesign emitted no DER entitlement slot for {bundle.name}")

    return {
        "abstract_sha256": sha256_bytes(abstract),
        "der_sha256": sha256_bytes(der),
        "der_slot_sha256": slot.group(1).lower(),
        "expected_strings_present": True,
        "xml_matches_expected": True,
        "xml_sha256": sha256_bytes(xml),
    }


def exercise(args: argparse.Namespace) -> dict[str, Any]:
    extracted = args.output_dir / "extracted"
    entitlements_dir = args.output_dir / "entitlements"
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    entitlements_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.fixture_ipa) as archive:
        archive.extractall(extracted)

    entitlements: dict[str, dict[str, Any]] = {}
    profile_hashes: dict[str, str] = {}
    entitlement_paths: dict[str, Path] = {}
    for role, (bundle_path, executable, bundle_identifier) in TARGETS.items():
        bundle = extracted / bundle_path
        executable_path = bundle / executable
        executable_path.chmod(0o755)
        profile_path = args.profiles_dir / f"{role}.mobileprovision"
        shutil.copyfile(profile_path, bundle / "embedded.mobileprovision")
        decoded = decode_profile(profile_path)
        profile_entitlements = decoded.get("Entitlements")
        if not isinstance(profile_entitlements, dict):
            raise CodesignOracleError(f"{role} profile has no entitlement dictionary")
        entitlements[role] = materialize_entitlements(role, bundle_identifier, profile_entitlements)
        entitlement_path = entitlements_dir / f"{role}.plist"
        entitlement_path.write_bytes(
            plistlib.dumps(entitlements[role], fmt=plistlib.FMT_XML, sort_keys=True)
        )
        entitlement_paths[role] = entitlement_path
        profile_hashes[role] = sha256_file(profile_path)

    order = signing_order()
    for role in order:
        bundle_path, _, _ = TARGETS[role]
        bundle = extracted / bundle_path
        run(
            [
                "codesign",
                "--force",
                "--sign",
                args.identity,
                "--keychain",
                str(args.keychain),
                "--timestamp=none",
                "--entitlements",
                str(entitlement_paths[role]),
                "--generate-entitlement-der",
                str(bundle),
            ]
        )
        run(["codesign", "--verify", "--strict", "--verbose=4", str(bundle)])

    root_bundle = extracted / TARGETS["root"][0]
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=4", str(root_bundle)])

    codesign_evidence: dict[str, dict[str, Any]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for role, (bundle_path, _, _) in TARGETS.items():
        bundle = extracted / bundle_path
        embedded_profile = bundle / "embedded.mobileprovision"
        profiles[role] = {
            "embedded_profile_sha256": sha256_file(embedded_profile),
            "profile_matches_input": sha256_file(embedded_profile) == profile_hashes[role],
            "profile_resource_seal_matches": profile_resource_seal_matches(
                bundle, embedded_profile.read_bytes()
            ),
        }
        if not profiles[role]["profile_matches_input"]:
            raise CodesignOracleError(f"{role} embedded profile differs from its input")
        if not profiles[role]["profile_resource_seal_matches"]:
            raise CodesignOracleError(f"{role} embedded profile has no matching resource seal")
        codesign_evidence[role] = inspect_codesign_entitlements(bundle, entitlements[role])

    violations = evaluate_contract(entitlements)
    return {
        "backend": "codesign",
        "codesign_evidence": codesign_evidence,
        "contract_pass": not violations,
        "nested_signature_verified": True,
        "profiles": profiles,
        "signed_entitlements": entitlement_evidence(entitlements),
        "signing_order": order,
        "source_fixture_sha256": sha256_file(args.fixture_ipa),
        "violations": violations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-ipa", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--keychain", type=Path, required=True)
    parser.add_argument("--profiles-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        summary = exercise(args)
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
        if not summary["contract_pass"]:
            return 2
        return 0
    except (
        BackendExerciseError,
        CodesignOracleError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as error:
        print(f"[codesign-oracle-error] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
