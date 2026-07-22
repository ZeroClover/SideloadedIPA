#!/usr/bin/env python3
"""Exercise upstream zsign's multi-profile behavior against the qualification IPA."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

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


def profile_resource_seal_matches(bundle: Path, profile: bytes) -> bool:
    document = plistlib.loads((bundle / "_CodeSignature" / "CodeResources").read_bytes())
    if not isinstance(document, Mapping):
        return False
    entries = document.get("files2")
    if not isinstance(entries, Mapping):
        return False
    entry = entries.get("embedded.mobileprovision")
    return (
        isinstance(entry, Mapping)
        and isinstance(entry.get("hash2"), bytes)
        and entry["hash2"] == hashlib.sha256(profile).digest()
    )


def zsign_command(
    zsign: Path,
    private_key: Path,
    certificate: Path,
    profiles_dir: Path,
    fixture_ipa: Path,
    signed_ipa: Path,
    entitlements_dir: Path | None = None,
) -> list[str]:
    command = [
        str(zsign),
        "-f",
        "-k",
        str(private_key),
        "-c",
        str(certificate),
    ]
    for role in TARGETS:
        command.extend(["-m", str(profiles_dir / f"{role}.mobileprovision")])
        if entitlements_dir is not None:
            command.extend(["-e", str(entitlements_dir / f"{role}.plist")])
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
) -> subprocess.CompletedProcess[str]:
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
    return result


def signing_order(output: str) -> list[str]:
    markers = {role: Path(bundle_path).name for role, (bundle_path, _, _) in TARGETS.items()}
    order: list[str] = []
    for line in output.splitlines():
        if "SignFolder:" not in line:
            continue
        for role, marker in markers.items():
            if marker in line and role not in order:
                order.append(role)
    return order


def rejects_mismatched_entitlement_count(command: Sequence[str]) -> bool:
    mismatched = list(command)
    entitlement_index = max(index for index, value in enumerate(mismatched) if value == "-e")
    del mismatched[entitlement_index : entitlement_index + 2]
    result = subprocess.run(
        mismatched,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    return (
        result.returncode != 0 and "Repeated entitlements must match the number and order" in output
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


def keychain_groups(application_identifier: str, bundle_identifier: str) -> list[str]:
    suffix = f".{bundle_identifier}"
    if not application_identifier.endswith(suffix):
        raise BackendExerciseError(
            f"application identifier does not match target bundle {bundle_identifier}"
        )
    prefix = application_identifier[: -len(bundle_identifier)]
    base = f"{prefix}com.kdt.livecontainer.shared"
    return [base, *(f"{base}.{index}" for index in range(1, 128))]


def authorizes(value: str, allowed_values: Sequence[str]) -> bool:
    return any(
        allowed == value or (allowed.endswith("*") and value.startswith(allowed[:-1]))
        for allowed in allowed_values
    )


def materialize_entitlements(
    role: str,
    bundle_identifier: str,
    profile_entitlements: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(profile_entitlements)
    if role not in {"root", "process"}:
        return result

    application_identifier = result.get("application-identifier")
    allowed_groups = result.get("keychain-access-groups")
    if not isinstance(application_identifier, str):
        raise BackendExerciseError(f"{role} profile has no application identifier")
    if not isinstance(allowed_groups, list) or not all(
        isinstance(item, str) for item in allowed_groups
    ):
        raise BackendExerciseError(f"{role} profile has invalid keychain authorization")

    expected_groups = keychain_groups(application_identifier, bundle_identifier)
    unauthorized = [value for value in expected_groups if not authorizes(value, allowed_groups)]
    if unauthorized:
        raise BackendExerciseError(
            f"{role} profile does not authorize {len(unauthorized)} expected keychain groups"
        )
    result["keychain-access-groups"] = expected_groups
    return result


def configured_entitlements(
    config_path: Path,
    role: str,
    profile_entitlements: Mapping[str, Any],
) -> dict[str, Any]:
    # The independent macOS oracle imports this module but does not use project
    # policy loading, so keep optional package dependencies on the canary path.
    from sideloadedipa.apple.intents import derive_bundle_resource_intents
    from sideloadedipa.config import (
        EntitlementTemplateContext,
        load_configuration,
        load_entitlement_template,
    )
    from sideloadedipa.domain import EntitlementMode
    from sideloadedipa.signing.profile_validation import validate_expected_entitlements

    configuration = load_configuration(config_path)
    tasks = tuple(task for task in configuration.tasks if task.task_name == "LiveContainer")
    if len(tasks) != 1 or tasks[0].signing is None:
        raise BackendExerciseError("configuration must contain one signed LiveContainer task")
    task = tasks[0]
    assert task.signing is not None
    _, _, target_bundle_id = TARGETS[role]
    intents = tuple(
        intent
        for intent in derive_bundle_resource_intents(task)
        if intent.target_bundle_id == target_bundle_id
    )
    if len(intents) != 1:
        raise BackendExerciseError(f"configuration has no exact {role} bundle policy")
    intent = intents[0]
    if intent.entitlement_policy.mode is EntitlementMode.PROFILE:
        return dict(profile_entitlements)
    if intent.entitlement_policy.mode is not EntitlementMode.TEMPLATE:
        raise BackendExerciseError(f"{role} canary policy must use profile or template mode")
    template_path = intent.entitlement_policy.template_path
    application_identifier = profile_entitlements.get("application-identifier")
    team_id = profile_entitlements.get("com.apple.developer.team-identifier")
    if not isinstance(application_identifier, str) or not application_identifier.endswith(
        target_bundle_id
    ):
        raise BackendExerciseError(f"{role} profile has no exact application identifier")
    if not isinstance(team_id, str) or not team_id:
        raise BackendExerciseError(f"{role} profile has no team identifier")
    if template_path is None:
        raise BackendExerciseError(f"{role} template policy has no template path")
    app_identifier_prefix = application_identifier[: -len(target_bundle_id)]
    expected = load_entitlement_template(
        config_path.resolve().parent.parent,
        template_path,
        EntitlementTemplateContext(
            team_id=team_id,
            app_identifier_prefix=app_identifier_prefix,
            target_bundle_id=target_bundle_id,
            app_groups=task.signing.app_groups,
        ),
    )
    validate_expected_entitlements(
        profile_entitlements,
        expected,
        bundle_id=target_bundle_id,
    )
    return expected


def decode_profile_entitlements(profile_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "openssl",
            "cms",
            "-verify",
            "-inform",
            "DER",
            "-noverify",
            "-in",
            str(profile_path),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise BackendExerciseError(f"cannot decode profile for {profile_path.stem}")
    document = plistlib.loads(result.stdout)
    if not isinstance(document, dict) or not isinstance(document.get("Entitlements"), dict):
        raise BackendExerciseError(f"profile {profile_path.stem} has no entitlement dictionary")
    return cast(dict[str, Any], document["Entitlements"])


def write_entitlement_files(
    profiles_dir: Path,
    entitlements_dir: Path,
    config_path: Path | None = None,
) -> None:
    entitlements_dir.mkdir(parents=True, exist_ok=True)
    for role, (_, _, bundle_identifier) in TARGETS.items():
        profile_entitlements = decode_profile_entitlements(profiles_dir / f"{role}.mobileprovision")
        entitlements = (
            configured_entitlements(config_path, role, profile_entitlements)
            if config_path is not None
            else materialize_entitlements(role, bundle_identifier, profile_entitlements)
        )
        (entitlements_dir / f"{role}.plist").write_bytes(
            plistlib.dumps(entitlements, fmt=plistlib.FMT_XML, sort_keys=True)
        )


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
                set(keychain_groups(application_identifier, bundle_identifier))
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


def entitlement_evidence(entitlements: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
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
    }


def exercise(args: argparse.Namespace) -> dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    signed_ipa = args.output_dir / "signed.ipa"
    entitlements_dir = None
    if args.per_profile_entitlements:
        entitlements_dir = args.output_dir / "expected-entitlements"
        write_entitlement_files(args.profiles_dir, entitlements_dir, args.config)
    command = zsign_command(
        args.zsign,
        args.private_key,
        args.certificate,
        args.profiles_dir,
        args.fixture_ipa,
        signed_ipa,
        entitlements_dir,
    )
    expected_entitlement_count = len(TARGETS) if args.per_profile_entitlements else 0
    if command.count("-m") != len(TARGETS) or command.count("-e") != expected_entitlement_count:
        raise BackendExerciseError("qualification command has an invalid profile/entitlement shape")
    mismatch_rejected = False
    if args.per_profile_entitlements:
        mismatch_rejected = rejects_mismatched_entitlement_count(command)
        if not mismatch_rejected:
            raise BackendExerciseError("backend accepted mismatched profile/entitlement counts")
    sign_result = run(command)
    order = signing_order(sign_result.stdout + sign_result.stderr)
    if set(order) != set(TARGETS) or order[-1:] != ["root"]:
        raise BackendExerciseError("backend did not report complete root-last signing order")

    extracted = args.output_dir / "extracted"
    with zipfile.ZipFile(signed_ipa) as archive:
        archive.extractall(extracted)

    entitlements: dict[str, dict[str, Any]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    resource_violations: list[str] = []
    for role, (bundle_path, executable, _) in TARGETS.items():
        bundle = extracted / bundle_path
        signed_profile = (bundle / "embedded.mobileprovision").read_bytes()
        expected_profile = (args.profiles_dir / f"{role}.mobileprovision").read_bytes()
        profile_matches = signed_profile == expected_profile
        profile_sealed = profile_resource_seal_matches(bundle, signed_profile)
        profiles[role] = {
            "embedded_profile_sha256": sha256_bytes(signed_profile),
            "profile_matches_input": profile_matches,
            "profile_resource_seal_matches": profile_sealed,
        }
        if not profile_matches:
            raise BackendExerciseError(f"{role} embedded profile does not match its input")
        if not profile_sealed:
            resource_violations.append(
                f"{role} embedded profile is not covered by its SHA-256 resource seal"
            )
        entitlements[role] = inspect_entitlements(
            args.zsign,
            bundle / executable,
            args.output_dir / "debug" / role,
        )

    violations = [*evaluate_contract(entitlements), *resource_violations]
    return {
        "backend": "zsign",
        "backend_variant": (
            "per-profile-entitlements-extension"
            if args.per_profile_entitlements
            else "upstream-profile-only"
        ),
        "command_shape": {
            "entitlement_count": command.count("-e"),
            "global_entitlements": False,
            "profile_count": command.count("-m"),
        },
        "contract_pass": not violations,
        "executable_sha256": sha256_bytes(args.zsign.read_bytes()),
        "mismatched_entitlement_count_rejected": mismatch_rejected,
        "profiles": profiles,
        "signed_entitlements": entitlement_evidence(entitlements),
        "signed_ipa_sha256": sha256_bytes(signed_ipa.read_bytes()),
        "signing_order": order,
        "violations": violations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zsign", type=Path, required=True)
    parser.add_argument("--fixture-ipa", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--profiles-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--per-profile-entitlements", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if args.per_profile_entitlements and args.config is None:
            raise BackendExerciseError("--config is required with --per-profile-entitlements")
        summary = exercise(args)
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
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
