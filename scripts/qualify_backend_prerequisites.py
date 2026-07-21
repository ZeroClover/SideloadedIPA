#!/usr/bin/env python3
"""Read-only qualification probe for the multi-bundle signing backend gate.

The probe downloads matching profiles into a private runner directory, validates
their identity/certificate/device relationships, and emits only redacted hashes
and entitlement key names. It never creates or deletes Apple resources.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

PROFILE_TYPE = "IOS_APP_DEVELOPMENT"
COMPATIBLE_DEVICE_CLASSES = {"IPHONE", "IPAD"}
TARGET_BUNDLE_IDS = {
    "root": "io.zeroclover.app.livecontainer",
    "process": "io.zeroclover.app.livecontainer.LiveProcess",
    "launch": "io.zeroclover.app.livecontainer.LaunchAppExtension",
    "share": "io.zeroclover.app.livecontainer.ShareExtension",
}


class QualificationError(RuntimeError):
    """A prerequisite cannot be proven without mutating Apple state."""


@dataclass(frozen=True)
class ProfileEvidence:
    role: str
    target_bundle_id: str
    profile_id: str
    profile_sha256: str
    certificate_sha256: tuple[str, ...]
    device_ids: frozenset[str]
    entitlement_keys: tuple[str, ...]
    app_groups: tuple[str, ...]

    def redacted(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "target_bundle_id": self.target_bundle_id,
            "profile_id_sha256": _sha256_text(self.profile_id),
            "profile_sha256": self.profile_sha256,
            "certificate_sha256": list(self.certificate_sha256),
            "device_count": len(self.device_ids),
            "entitlement_keys": list(self.entitlement_keys),
            "app_groups": list(self.app_groups),
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _json_object(raw: str, command: Sequence[str]) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise QualificationError(f"asc returned invalid JSON for {command[1:3]}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"asc returned a non-object for {command[1:3]}")
    return value


def run_json(args: Sequence[str]) -> dict[str, Any]:
    command = ["asc", *args, "--output", "json"]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "TMPDIR",
                "ASC_KEY_ID",
                "ASC_ISSUER_ID",
                "ASC_PRIVATE_KEY_B64",
                "ASC_BYPASS_KEYCHAIN",
            }
        },
    )
    if result.returncode != 0:
        raise QualificationError(
            f"asc command {args[:2]} failed with exit code {result.returncode}"
        )
    return _json_object(result.stdout, command)


def data_list(document: Mapping[str, Any], resource: str) -> list[dict[str, Any]]:
    data = document.get("data")
    if not isinstance(data, list):
        raise QualificationError(f"{resource} response has no data list")
    if not all(isinstance(item, dict) for item in data):
        raise QualificationError(f"{resource} response contains a non-object item")
    return data


def exact_bundle_resources(
    bundles: Sequence[Mapping[str, Any]], targets: Mapping[str, str]
) -> dict[str, str]:
    by_identifier: dict[str, list[str]] = {}
    for bundle in bundles:
        resource_id = bundle.get("id")
        attributes = bundle.get("attributes")
        if not isinstance(resource_id, str) or not isinstance(attributes, dict):
            continue
        identifier = attributes.get("identifier")
        if isinstance(identifier, str):
            by_identifier.setdefault(identifier, []).append(resource_id)

    resolved: dict[str, str] = {}
    problems: list[str] = []
    for role, identifier in targets.items():
        candidates = by_identifier.get(identifier, [])
        if len(candidates) != 1:
            problems.append(f"{role}:{identifier} has {len(candidates)} exact App IDs")
        else:
            resolved[role] = candidates[0]
    if problems:
        raise QualificationError("; ".join(problems))
    return resolved


def profile_bundle_resource_id(profile: Mapping[str, Any]) -> str | None:
    relationships = profile.get("relationships")
    if not isinstance(relationships, dict):
        return None
    bundle = relationships.get("bundleId")
    if not isinstance(bundle, dict):
        return None
    data = bundle.get("data")
    if not isinstance(data, dict):
        return None
    resource_id = data.get("id")
    return resource_id if isinstance(resource_id, str) else None


def profile_type(profile: Mapping[str, Any]) -> str | None:
    attributes = profile.get("attributes")
    if not isinstance(attributes, dict):
        return None
    value = attributes.get("profileType")
    return value if isinstance(value, str) else None


def profile_state(profile: Mapping[str, Any]) -> str | None:
    attributes = profile.get("attributes")
    if not isinstance(attributes, dict):
        return None
    value = attributes.get("profileState")
    return value if isinstance(value, str) else None


def resolve_profile_bundle_resource_id(profile: Mapping[str, Any]) -> str | None:
    embedded = profile_bundle_resource_id(profile)
    if embedded:
        return embedded
    resource_id = profile.get("id")
    if not isinstance(resource_id, str) or not resource_id:
        return None
    linked = run_json(["profiles", "links", "bundle-id", "--id", resource_id]).get("data")
    if not isinstance(linked, dict):
        return None
    linked_id = linked.get("id")
    return linked_id if isinstance(linked_id, str) else None


def select_profiles(
    profiles: Sequence[Mapping[str, Any]], bundle_resources: Mapping[str, str]
) -> dict[str, str]:
    by_bundle: dict[str, list[str]] = {resource_id: [] for resource_id in bundle_resources.values()}
    for profile in profiles:
        if profile_type(profile) != PROFILE_TYPE or profile_state(profile) != "ACTIVE":
            continue
        profile_id = profile.get("id")
        if not isinstance(profile_id, str):
            continue
        bundle_resource_id = resolve_profile_bundle_resource_id(profile)
        if bundle_resource_id in by_bundle:
            by_bundle[bundle_resource_id].append(profile_id)

    selected: dict[str, str] = {}
    problems: list[str] = []
    for role, bundle_resource_id in bundle_resources.items():
        candidates = by_bundle[bundle_resource_id]
        if len(candidates) != 1:
            problems.append(f"{role} has {len(candidates)} active development profiles")
        else:
            selected[role] = candidates[0]
    if problems:
        raise QualificationError("; ".join(problems))
    return selected


def decode_profile(profile_path: Path, output_path: Path) -> dict[str, Any]:
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
            "-out",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise QualificationError(f"cannot decode profile for {profile_path.stem}")
    value = plistlib.loads(output_path.read_bytes())
    if not isinstance(value, dict):
        raise QualificationError(f"decoded profile for {profile_path.stem} is not a dictionary")
    return value


def download_profile(profile_id: str, output_path: Path) -> None:
    document = run_json(
        [
            "profiles",
            "view",
            "--id",
            profile_id,
            "--include",
            "bundleId,certificates,devices",
        ]
    )
    data = document.get("data")
    if not isinstance(data, dict):
        raise QualificationError("profile view response has no data object")
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        raise QualificationError("profile view response has no attributes")
    content = attributes.get("profileContent")
    if not isinstance(content, str) or not content:
        raise QualificationError("profile view response has no profileContent")
    try:
        decoded = base64.b64decode(content, validate=True)
    except ValueError as error:
        raise QualificationError("profileContent is not valid base64") from error
    output_path.write_bytes(decoded)


def profile_evidence(
    role: str,
    target_bundle_id: str,
    profile_id: str,
    profile_path: Path,
    decoded: Mapping[str, Any],
) -> ProfileEvidence:
    entitlements = decoded.get("Entitlements")
    if not isinstance(entitlements, dict):
        raise QualificationError(f"{role} profile has no entitlement dictionary")
    team_ids = decoded.get("TeamIdentifier")
    if not isinstance(team_ids, list) or len(team_ids) != 1 or not isinstance(team_ids[0], str):
        raise QualificationError(f"{role} profile does not contain exactly one team identifier")
    expected_application_id = f"{team_ids[0]}.{target_bundle_id}"
    if entitlements.get("application-identifier") != expected_application_id:
        raise QualificationError(f"{role} profile application identifier does not match target")

    certificates = decoded.get("DeveloperCertificates")
    if not isinstance(certificates, list) or not certificates:
        raise QualificationError(f"{role} profile has no developer certificates")
    certificate_hashes = tuple(
        sorted(_sha256_bytes(bytes(certificate)) for certificate in certificates)
    )

    device_ids = decoded.get("ProvisionedDevices")
    if not isinstance(device_ids, list) or not all(isinstance(item, str) for item in device_ids):
        raise QualificationError(f"{role} profile has no provisioned device set")

    groups = entitlements.get("com.apple.security.application-groups", [])
    if not isinstance(groups, list) or not all(isinstance(item, str) for item in groups):
        raise QualificationError(f"{role} profile has invalid App Groups authorization")

    return ProfileEvidence(
        role=role,
        target_bundle_id=target_bundle_id,
        profile_id=profile_id,
        profile_sha256=_sha256_bytes(profile_path.read_bytes()),
        certificate_sha256=certificate_hashes,
        device_ids=frozenset(device_ids),
        entitlement_keys=tuple(sorted(str(key) for key in entitlements)),
        app_groups=tuple(sorted(groups)),
    )


def certificate_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def ensure_common_contract(
    evidence: Sequence[ProfileEvidence], p12_certificate_sha256: str
) -> dict[str, Any]:
    if not all(p12_certificate_sha256 in item.certificate_sha256 for item in evidence):
        raise QualificationError("configured P12 certificate is not present in every profile")

    common_devices = set.intersection(*(set(item.device_ids) for item in evidence))
    if not common_devices:
        raise QualificationError("profiles have no common registered iOS device")

    common_groups = set.intersection(*(set(item.app_groups) for item in evidence))
    if not common_groups:
        raise QualificationError("profiles have no common authorized App Group")

    return {
        "common_device_count": len(common_devices),
        "common_app_groups": sorted(common_groups),
        "p12_certificate_sha256": p12_certificate_sha256,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--certificate-der", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    devices = data_list(
        run_json(
            [
                "devices",
                "list",
                "--platform",
                "IOS",
                "--status",
                "ENABLED",
                "--paginate",
            ]
        ),
        "devices",
    )
    compatible_devices = {
        item["id"]
        for item in devices
        if isinstance(item.get("id"), str)
        and isinstance(item.get("attributes"), dict)
        and item["attributes"].get("deviceClass") in COMPATIBLE_DEVICE_CLASSES
    }
    if not compatible_devices:
        raise QualificationError("no enabled iPhone or iPad is registered")

    bundles = data_list(run_json(["bundle-ids", "list", "--paginate"]), "bundle IDs")
    bundle_resources = exact_bundle_resources(bundles, TARGET_BUNDLE_IDS)
    profiles = data_list(
        run_json(
            [
                "profiles",
                "list",
                "--profile-type",
                PROFILE_TYPE,
                "--profile-state",
                "ACTIVE",
                "--paginate",
            ]
        ),
        "profiles",
    )
    selected_profiles = select_profiles(profiles, bundle_resources)

    evidence: list[ProfileEvidence] = []
    for role, profile_id in selected_profiles.items():
        profile_path = args.private_dir / f"{role}.mobileprovision"
        plist_path = args.private_dir / f"{role}.plist"
        download_profile(profile_id, profile_path)
        decoded = decode_profile(profile_path, plist_path)
        evidence.append(
            profile_evidence(
                role,
                TARGET_BUNDLE_IDS[role],
                profile_id,
                profile_path,
                decoded,
            )
        )

    common = ensure_common_contract(evidence, certificate_sha256(args.certificate_der))
    summary = {
        "schema_version": 1,
        "ready": True,
        "enabled_ios_device_count": len(compatible_devices),
        "profiles": [item.redacted() for item in sorted(evidence, key=lambda item: item.role)],
        **common,
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as error:
        print(f"[qualification-error] {error}", file=sys.stderr)
        raise SystemExit(2) from error
