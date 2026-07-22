"""Tests for Linux-compatible Mach-O entitlement inspection."""

from __future__ import annotations

import math
import plistlib
import struct
from pathlib import Path

import pytest

from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import LiefEntitlementInspector, decode_der_entitlements

DER_ENTITLEMENTS = bytes.fromhex(
    "708191020101b0818b"
    "30340c166170706c69636174696f6e2d6964656e746966696572"
    "0c1a5445414d49442e636f6d2e6578616d706c652e66697874757265"
    "30130c0e6765742d7461736b2d616c6c6f770101ff"
    "303e0c166b6579636861696e2d6163636573732d67726f757073"
    "30240c105445414d49442e67726f75702e6f6e65"
    "0c105445414d49442e67726f75702e74776f"
)
NESTED_DER_ENTITLEMENTS = bytes.fromhex(
    "703a020101b035"
    "30330c066e6573746564b029"
    "300a0c05636f756e74020103"
    "300c0c07656e61626c65640101ff"
    "300d0c046e616d650c0576616c7565"
)
EXPECTED = {
    "application-identifier": "TEAMID.com.example.fixture",
    "get-task-allow": True,
    "keychain-access-groups": ["TEAMID.group.one", "TEAMID.group.two"],
}


def make_superblob(*, xml: bytes | None, der: bytes | None) -> bytes:
    blobs: list[tuple[int, bytes]] = []
    if xml is not None:
        blobs.append((5, struct.pack(">II", 0xFADE7171, len(xml) + 8) + xml))
    if der is not None:
        blobs.append((7, struct.pack(">II", 0xFADE7172, len(der) + 8) + der))
    offset = 12 + len(blobs) * 8
    indexes = bytearray()
    payload = bytearray()
    for slot, blob in blobs:
        indexes.extend(struct.pack(">II", slot, offset))
        payload.extend(blob)
        offset += len(blob)
    length = 12 + len(indexes) + len(payload)
    return struct.pack(">III", 0xFADE0CC0, length, len(blobs)) + indexes + payload


def make_thin(signature: bytes, cpu_type: int = 0x0100000C) -> bytes:
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, cpu_type, 0, 2, 2, 88, 0, 0)
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


def make_fat(first: bytes, second: bytes) -> bytes:
    first_offset = 0x1000
    second_offset = 0x2000
    header = struct.pack(">II", 0xCAFEBABE, 2)
    arches = struct.pack(
        ">IIIIIIIIII",
        0x0100000C,
        0,
        first_offset,
        len(first),
        12,
        0x01000007,
        3,
        second_offset,
        len(second),
        12,
    )
    return (
        header
        + arches
        + bytes(first_offset - len(header) - len(arches))
        + first
        + bytes(second_offset - first_offset - len(first))
        + second
    )


def entitlement_superblob() -> bytes:
    xml = plistlib.dumps(EXPECTED, fmt=plistlib.FMT_XML, sort_keys=True)
    return make_superblob(xml=xml, der=DER_ENTITLEMENTS)


def test_decodes_real_codesign_der_fixture() -> None:
    assert decode_der_entitlements(DER_ENTITLEMENTS) == EXPECTED


def test_decodes_nested_dictionary_and_integer_from_codesign_der() -> None:
    assert decode_der_entitlements(NESTED_DER_ENTITLEMENTS) == {
        "nested": {"count": 3, "enabled": True, "name": "value"}
    }


def test_inspects_xml_and_der_from_thin_macho(tmp_path: Path) -> None:
    executable = tmp_path / "thin"
    executable.write_bytes(make_thin(entitlement_superblob()))

    evidence = LiefEntitlementInspector().inspect(executable)

    assert len(evidence.slices) == 1
    assert evidence.slices[0].architecture == "ARM64:0:0"
    assert evidence.slices[0].xml == EXPECTED
    assert evidence.slices[0].der == EXPECTED
    assert evidence.slices[0].xml_raw is not None
    assert evidence.slices[0].der_raw == DER_ENTITLEMENTS


def test_inspects_every_slice_in_fat_macho(tmp_path: Path) -> None:
    signature = entitlement_superblob()
    executable = tmp_path / "fat"
    executable.write_bytes(
        make_fat(
            make_thin(signature, 0x0100000C),
            make_thin(signature, 0x01000007),
        )
    )

    evidence = LiefEntitlementInspector().inspect(executable)

    assert [item.architecture for item in evidence.slices] == [
        "ARM64:0:0",
        "X86_64:0:1",
    ]
    assert all(item.xml == EXPECTED and item.der == EXPECTED for item in evidence.slices)


@pytest.mark.parametrize(
    "signature, message",
    [
        (make_superblob(xml=b"not a plist", der=None), "Invalid file"),
        (make_superblob(xml=None, der=DER_ENTITLEMENTS[:-1]), "substrate"),
    ],
)
def test_rejects_unreadable_entitlement_evidence(
    tmp_path: Path, signature: bytes, message: str
) -> None:
    executable = tmp_path / "invalid"
    executable.write_bytes(make_thin(signature))

    with pytest.raises(DomainError) as caught:
        LiefEntitlementInspector().inspect(executable)

    assert caught.value.code is ErrorCode.INVENTORY_ENTITLEMENTS_INVALID
    assert message.lower() in caught.value.message.lower()
    assert dict(caught.value.safe_details)["path"] == str(executable)


def test_rejects_unsigned_macho(tmp_path: Path) -> None:
    executable = tmp_path / "unsigned"
    executable.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))

    with pytest.raises(DomainError, match="no embedded code signature") as caught:
        LiefEntitlementInspector().inspect(executable)

    assert dict(caught.value.safe_details)["reason"] == "missing-code-signature"


@pytest.mark.parametrize(
    "document, message",
    [
        ({"unsupported": b"bytes"}, "unsupported entitlement plist type"),
        ({"not-finite": math.nan}, "non-finite number"),
    ],
)
def test_rejects_unsupported_xml_entitlement_values(
    tmp_path: Path, document: dict[str, object], message: str
) -> None:
    executable = tmp_path / "invalid-xml"
    xml = plistlib.dumps(document, fmt=plistlib.FMT_XML)
    executable.write_bytes(make_thin(make_superblob(xml=xml, der=None)))

    with pytest.raises(DomainError, match=message):
        LiefEntitlementInspector().inspect(executable)


def test_rejects_unsupported_der_version(tmp_path: Path) -> None:
    executable = tmp_path / "invalid-der-version"
    version_two = DER_ENTITLEMENTS.replace(b"\x02\x01\x01", b"\x02\x01\x02", 1)
    executable.write_bytes(make_thin(make_superblob(xml=None, der=version_two)))

    with pytest.raises(DomainError, match="unsupported entitlement DER version"):
        LiefEntitlementInspector().inspect(executable)


def test_rejects_duplicate_entitlement_slot_and_wrong_blob_magic(tmp_path: Path) -> None:
    xml = plistlib.dumps(EXPECTED, fmt=plistlib.FMT_XML)
    blob = struct.pack(">II", 0xFADE7171, len(xml) + 8) + xml
    duplicate_slot = (
        struct.pack(">III", 0xFADE0CC0, 28 + len(blob), 2)
        + struct.pack(">IIII", 5, 28, 5, 28)
        + blob
    )
    executable = tmp_path / "duplicate-slot"
    executable.write_bytes(make_thin(duplicate_slot))
    with pytest.raises(DomainError, match="duplicate entitlement slot"):
        LiefEntitlementInspector().inspect(executable)

    wrong_magic = bytearray(make_superblob(xml=xml, der=None))
    wrong_magic[20:24] = struct.pack(">I", 0xFADE7172)
    executable.write_bytes(make_thin(bytes(wrong_magic)))
    with pytest.raises(DomainError, match="unexpected magic"):
        LiefEntitlementInspector().inspect(executable)
