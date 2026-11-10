"""Stand-in for the App Store Connect CLI, installed on PATH as ``asc``.

Apple is the one service the end-to-end suite cannot reach, so this executable
answers the pinned ``asc`` command surface from a JSON state file beside it.
It issues real CMS-signed provisioning profiles, which the pipeline then
validates, embeds and verifies exactly as it would Apple's.

The pipeline strips the child environment to an allowlist, so the state
directory is located from the script path rather than from a variable.
"""

from __future__ import annotations

import base64
import json
import plistlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7

VERSION = "6.0.0 (commit: e2e, date: 2026-01-01T00:00:00Z)"
CAPABILITY_ENTITLEMENTS: dict[str, tuple[str, object]] = {
    "HEALTHKIT": ("com.apple.developer.healthkit", True),
    "INCREASED_MEMORY_LIMIT": ("com.apple.developer.kernel.increased-memory-limit", True),
}
EXIT_USAGE = 2
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5

STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
STATE_PATH = STATE_ROOT / "state.json"
CALLS_PATH = STATE_ROOT / "calls.jsonl"


class CommandFailure(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def option(argv: list[str], name: str) -> str:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError) as error:
        raise CommandFailure(EXIT_USAGE, f"missing required flag {name}") from error


def find(resources: list[dict[str, Any]], resource_id: str, kind: str) -> dict[str, Any]:
    for resource in resources:
        if resource["id"] == resource_id:
            return resource
    raise CommandFailure(EXIT_NOT_FOUND, f"{kind} {resource_id} was not found")


def bundle_resource(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "bundleIds",
        "id": bundle["id"],
        "attributes": {
            "identifier": bundle["identifier"],
            "name": bundle["name"],
            "platform": bundle["platform"],
            "seedId": bundle["seedId"],
        },
    }


def capability_resource(capability: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "bundleIdCapabilities",
        "id": capability["id"],
        "attributes": {"capabilityType": capability["capabilityType"], "settings": []},
    }


def profile_resource(profile: dict[str, Any], *, relationships: bool) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "type": "profiles",
        "id": profile["id"],
        "attributes": {
            "name": profile["name"],
            "platform": "IOS",
            "profileType": profile["profileType"],
            "profileState": "ACTIVE",
            "uuid": profile["uuid"],
            "createdDate": profile["createdDate"],
            "expirationDate": profile["expirationDate"],
            "profileContent": profile["profileContent"],
        },
    }
    if relationships:
        resource["relationships"] = {
            "bundleId": {"data": {"type": "bundleIds", "id": profile["bundle"]}},
            "certificates": {
                "data": [{"type": "certificates", "id": value} for value in profile["certificates"]]
            },
            "devices": {"data": [{"type": "devices", "id": value} for value in profile["devices"]]},
        }
    return resource


def issue_profile(
    state: dict[str, Any],
    *,
    name: str,
    bundle: dict[str, Any],
    certificates: list[dict[str, Any]],
    devices: list[dict[str, Any]],
) -> tuple[str, bytes, datetime, datetime]:
    """Build and CMS-sign a development profile for the current App ID state."""

    application_identifier = f"{bundle['seedId']}.{bundle['identifier']}"
    entitlements: dict[str, object] = {
        "application-identifier": application_identifier,
        "com.apple.developer.team-identifier": state["team_id"],
        "get-task-allow": True,
        "keychain-access-groups": [f"{bundle['seedId']}.*"],
    }
    for capability in state["capabilities"]:
        if capability["bundle"] == bundle["id"]:
            key, value = CAPABILITY_ENTITLEMENTS[capability["capabilityType"]]
            entitlements[key] = value
    profile_uuid = str(uuid.uuid4())
    created = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
    expires = created + timedelta(days=365)
    document = {
        "UUID": profile_uuid,
        "Name": name,
        "TeamIdentifier": [state["team_id"]],
        "ApplicationIdentifierPrefix": [bundle["seedId"]],
        "CreationDate": created.replace(tzinfo=None),
        "ExpirationDate": expires.replace(tzinfo=None),
        "DeveloperCertificates": [
            base64.b64decode(certificate["certificateContent"]) for certificate in certificates
        ],
        "ProvisionedDevices": [device["udid"] for device in devices],
        "Entitlements": entitlements,
    }
    signer = (STATE_ROOT / "profile-signer.pem").read_bytes()
    content = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True))
        .add_signer(
            x509.load_pem_x509_certificate(signer),
            serialization.load_pem_private_key(signer, password=None),
            hashes.SHA256(),
        )
        .sign(Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )
    return profile_uuid, content, created, expires


def execute(argv: list[str], state: dict[str, Any]) -> dict[str, Any]:
    command = [value for value in argv if not value.startswith("--")][:3]
    if command[:2] == ["bundle-ids", "list"]:
        return {"data": [bundle_resource(value) for value in state["bundle_ids"]]}
    if command[:2] == ["bundle-ids", "create"]:
        identifier = option(argv, "--identifier")
        if any(
            value["identifier"].casefold() == identifier.casefold() for value in state["bundle_ids"]
        ):
            raise CommandFailure(EXIT_CONFLICT, "an App ID with this identifier already exists")
        bundle = {
            "id": f"BUNDLE{len(state['bundle_ids']) + 1:04d}",
            "identifier": identifier,
            "name": option(argv, "--name"),
            "platform": option(argv, "--platform"),
            "seedId": state["team_id"],
        }
        state["bundle_ids"].append(bundle)
        return {"data": bundle_resource(bundle)}
    if command == ["bundle-ids", "capabilities", "list"]:
        bundle = find(state["bundle_ids"], option(argv, "--bundle"), "bundle ID")
        return {
            "data": [
                capability_resource(value)
                for value in state["capabilities"]
                if value["bundle"] == bundle["id"]
            ]
        }
    if command == ["bundle-ids", "capabilities", "add"]:
        bundle = find(state["bundle_ids"], option(argv, "--bundle"), "bundle ID")
        capability_type = option(argv, "--capability")
        if capability_type not in CAPABILITY_ENTITLEMENTS:
            raise CommandFailure(EXIT_USAGE, f"unsupported capability {capability_type}")
        if any(
            value["bundle"] == bundle["id"] and value["capabilityType"] == capability_type
            for value in state["capabilities"]
        ):
            raise CommandFailure(EXIT_CONFLICT, "capability is already enabled")
        capability = {
            "id": f"{bundle['id']}_{capability_type}",
            "bundle": bundle["id"],
            "capabilityType": capability_type,
        }
        state["capabilities"].append(capability)
        return {"data": capability_resource(capability)}
    if command[:2] == ["certificates", "list"]:
        return {
            "data": [
                {
                    "type": "certificates",
                    "id": value["id"],
                    "attributes": {key: item for key, item in value.items() if key != "id"},
                }
                for value in state["certificates"]
            ]
        }
    if command[:2] == ["devices", "list"]:
        return {
            "data": [
                {
                    "type": "devices",
                    "id": value["id"],
                    "attributes": {key: item for key, item in value.items() if key != "id"},
                }
                for value in state["devices"]
                if value["status"] == option(argv, "--status")
            ]
        }
    if command[:2] == ["profiles", "list"]:
        return {
            "data": [
                profile_resource(value, relationships=False)
                for value in state["profiles"]
                if value["profileType"] == option(argv, "--profile-type")
            ]
        }
    if command[:2] == ["profiles", "view"]:
        profile = find(state["profiles"], option(argv, "--id"), "profile")
        return {"data": profile_resource(profile, relationships=True)}
    if command[:2] == ["profiles", "create"]:
        bundle = find(state["bundle_ids"], option(argv, "--bundle"), "bundle ID")
        certificates = [
            find(state["certificates"], value, "certificate")
            for value in option(argv, "--certificate").split(",")
        ]
        devices = [
            find(state["devices"], value, "device") for value in option(argv, "--device").split(",")
        ]
        name = option(argv, "--name")
        profile_uuid, content, created, expires = issue_profile(
            state, name=name, bundle=bundle, certificates=certificates, devices=devices
        )
        profile = {
            "id": f"PROFILE{len(state['profiles']) + 1:04d}",
            "name": name,
            "profileType": option(argv, "--profile-type"),
            "uuid": profile_uuid,
            "createdDate": created.isoformat(),
            "expirationDate": expires.isoformat(),
            "profileContent": base64.b64encode(content).decode(),
            "bundle": bundle["id"],
            "certificates": [value["id"] for value in certificates],
            "devices": [value["id"] for value in devices],
        }
        state["profiles"].append(profile)
        return {"data": profile_resource(profile, relationships=True)}
    raise CommandFailure(EXIT_USAGE, f"unknown command: {' '.join(command)}")


def main(argv: list[str]) -> int:
    with CALLS_PATH.open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")
    if argv == ["version"]:
        print(VERSION)
        return 0
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    try:
        document = execute(argv, state)
    except CommandFailure as failure:
        print(f"Error: {failure}", file=sys.stderr)
        return failure.exit_code
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
