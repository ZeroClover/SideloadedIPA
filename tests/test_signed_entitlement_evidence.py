"""Tests for independent entitlement extraction from signed IPAs."""

from __future__ import annotations

import hashlib
import plistlib
import struct
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.verification import inspect_signed_entitlements

EXPECTED = {
    "application-identifier": "TEAMID.com.example.fixture",
    "get-task-allow": True,
    "keychain-access-groups": ["TEAMID.group.one", "TEAMID.group.two"],
}
DER_ENTITLEMENTS = bytes.fromhex(
    "708191020101b0818b"
    "30340c166170706c69636174696f6e2d6964656e746966696572"
    "0c1a5445414d49442e636f6d2e6578616d706c652e66697874757265"
    "30130c0e6765742d7461736b2d616c6c6f770101ff"
    "303e0c166b6579636861696e2d6163636573732d67726f757073"
    "30240c105445414d49442e67726f75702e6f6e65"
    "0c105445414d49442e67726f75702e74776f"
)


def superblob() -> bytes:
    xml = plistlib.dumps(EXPECTED, fmt=plistlib.FMT_XML, sort_keys=True)
    blobs = (
        (5, struct.pack(">II", 0xFADE7171, len(xml) + 8) + xml),
        (7, struct.pack(">II", 0xFADE7172, len(DER_ENTITLEMENTS) + 8) + DER_ENTITLEMENTS),
    )
    offset = 12 + len(blobs) * 8
    indexes = bytearray()
    payload = bytearray()
    for slot, blob in blobs:
        indexes.extend(struct.pack(">II", slot, offset))
        payload.extend(blob)
        offset += len(blob)
    length = 12 + len(indexes) + len(payload)
    return struct.pack(">III", 0xFADE0CC0, length, len(blobs)) + indexes + payload


def signed_macho() -> bytes:
    signature = superblob()
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 2, 88, 0, 0)
    signature_offset = len(header) + 88
    segment = struct.pack(
        "<II16sQQQQIIII",
        0x19,
        72,
        b"__LINKEDIT",
        0,
        len(signature),
        signature_offset,
        len(signature),
        1,
        1,
        0,
        0,
    )
    command = struct.pack("<IIII", 0x1D, 16, signature_offset, len(signature))
    return header + segment + command + signature


def signed_macho_without_entitlements() -> bytes:
    signature = struct.pack(">III", 0xFADE0CC0, 12, 0)
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 2, 88, 0, 0)
    signature_offset = len(header) + 88
    segment = struct.pack(
        "<II16sQQQQIIII",
        0x19,
        72,
        b"__LINKEDIT",
        0,
        len(signature),
        signature_offset,
        len(signature),
        1,
        1,
        0,
        0,
    )
    command = struct.pack("<IIII", 0x1D, 16, signature_offset, len(signature))
    return header + segment + command + signature


def plan() -> SigningPlan:
    expected = normalize_entitlements(EXPECTED)
    root = PurePosixPath("Payload/App.app")
    return SigningPlan(
        "Example",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        SigningBackendIdentity("fixture", "1", "d" * 64, "1"),
        (
            SigningNodePlan(
                root,
                root / "UnexpectedExecutableName",
                BundleNodeKind.APP,
                0,
                "io.example.app",
                "PROFILE",
                PurePosixPath("Example/profile.mobileprovision"),
                "e" * 64,
                expected.values,
                expected.sha256,
            ),
        ),
        "f" * 64,
    )


def signed_ipa(path: Path, *, executable: bytes | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Payload/App.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": "io.example.app"}),
        )
        if executable is not None:
            archive.writestr("Payload/App.app/UnexpectedExecutableName", executable)


def test_reopens_ipa_and_records_xml_and_der_evidence_per_executable(tmp_path: Path) -> None:
    artifact = tmp_path / "signed.ipa"
    signed_ipa(artifact, executable=signed_macho())
    original = artifact.read_bytes()

    extracted = tmp_path / "signed"
    extract_ipa_safely(artifact, extracted)
    evidence = inspect_signed_entitlements(
        plan(),
        extracted,
        hashlib.sha256(original).hexdigest(),
    )

    assert artifact.read_bytes() == original
    assert evidence.plan_sha256 == plan().plan_sha256
    assert evidence.artifact_sha256 == hashlib.sha256(original).hexdigest()
    assert len(evidence.nodes) == 1
    node = evidence.nodes[0]
    assert node.executable_path.name == "UnexpectedExecutableName"
    assert node.executable_sha256 == hashlib.sha256(signed_macho()).hexdigest()
    assert len(node.slices) == 1
    assert node.slices[0].xml is not None
    assert node.slices[0].der is not None
    assert node.slices[0].xml.semantic_sha256 == node.slices[0].der.semantic_sha256
    assert node.slices[0].xml.raw_sha256 != node.slices[0].der.raw_sha256


def test_records_a_signed_slice_with_no_entitlement_slots(tmp_path: Path) -> None:
    artifact = tmp_path / "signed.ipa"
    signed_ipa(artifact, executable=signed_macho_without_entitlements())

    extracted = tmp_path / "signed"
    extract_ipa_safely(artifact, extracted)
    evidence = inspect_signed_entitlements(
        plan(),
        extracted,
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )

    assert len(evidence.nodes[0].slices) == 1
    assert evidence.nodes[0].slices[0].xml is None
    assert evidence.nodes[0].slices[0].der is None


@pytest.mark.parametrize("failure", ["missing", "unsigned", "unsafe-path"])
def test_fails_closed_when_executable_evidence_is_unreadable(tmp_path: Path, failure: str) -> None:
    artifact = tmp_path / "signed.ipa"
    signed_ipa(artifact, executable=None if failure == "missing" else b"unsigned")
    signing_plan = plan()
    if failure == "unsafe-path":
        signing_plan = replace(
            signing_plan,
            nodes=(replace(signing_plan.nodes[0], executable_path=PurePosixPath("../escape")),),
        )
    extracted = tmp_path / "signed"
    extract_ipa_safely(artifact, extracted)

    with pytest.raises(DomainError) as caught:
        inspect_signed_entitlements(
            signing_plan,
            extracted,
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )

    assert caught.value.code is ErrorCode.VERIFICATION_EVIDENCE_INVALID
