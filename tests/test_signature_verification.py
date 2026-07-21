"""Tests for cryptographic Mach-O and nested bundle-seal verification."""

from __future__ import annotations

import hashlib
import plistlib
import struct
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509.oid import NameOID
from pyasn1.codec.der import decoder, encoder
from pyasn1.type import univ
from pyasn1_modules import rfc5652

from sideloadedipa.domain import (
    BundleNodeKind,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.verification import signatures as signature_module
from sideloadedipa.verification import verify_signed_signatures


def certificate() -> tuple[rsa.RSAPrivateKey, x509.Certificate, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signature Fixture")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    digest = hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()
    return key, cert, digest


def code_directory(
    code: bytes,
    identifier: str,
    *,
    info: bytes | None = None,
    resources: bytes | None = None,
    adhoc: bool = False,
) -> bytes:
    identifier_bytes = identifier.encode() + b"\0"
    team_bytes = b"TEAMID1234\0"
    header_size = 88
    special = (
        hashlib.sha256(resources).digest() if resources is not None else bytes(32),
        bytes(32),
        hashlib.sha256(info).digest() if info is not None else bytes(32),
    )
    special_count = 3 if info is not None and resources is not None else 0
    special_bytes = b"".join(special[-special_count:]) if special_count else b""
    code_hashes = b"".join(
        hashlib.sha256(code[offset : offset + 4096]).digest()
        for offset in range(0, len(code), 4096)
    )
    hash_offset = header_size + len(identifier_bytes) + len(team_bytes) + len(special_bytes)
    length = hash_offset + len(code_hashes)
    return b"".join(
        (
            struct.pack(
                ">IIIIIIIIIBBBBI",
                0xFADE0C02,
                length,
                0x20400,
                0x2 if adhoc else 0,
                hash_offset,
                header_size,
                special_count,
                len(code_hashes) // 32,
                len(code),
                32,
                2,
                0,
                12,
                0,
            ),
            struct.pack(">IIIQQQQ", 0, header_size + len(identifier_bytes), 0, 0, 0, len(code), 1),
            identifier_bytes,
            team_bytes,
            special_bytes,
            code_hashes,
        )
    )


def cms_signature(
    content: bytes,
    key: rsa.RSAPrivateKey,
    cert: x509.Certificate,
) -> bytes:
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(content)
        .add_signer(cert, key, hashes.SHA256())
        .sign(
            serialization.Encoding.DER,
            [
                pkcs7.PKCS7Options.Binary,
                pkcs7.PKCS7Options.DetachedSignature,
                pkcs7.PKCS7Options.NoCapabilities,
            ],
        )
    )


def cms_with_cdhash_attributes(
    cms: bytes,
    primary: Any,
    alternate: Any,
    *,
    wrong_plist: bool = False,
    wrong_algorithm: bool = False,
    wrong_full_digest: bool = False,
) -> bytes:
    content_info, _ = decoder.decode(cms, asn1Spec=rfc5652.ContentInfo())
    signed_data, _ = decoder.decode(content_info["content"], asn1Spec=rfc5652.SignedData())
    primary_directory = primary
    alternate_directory = alternate
    expected_cdhashes = [primary_directory.cdhash, alternate_directory.cdhash]
    if wrong_plist:
        expected_cdhashes[-1] = bytes(20)
    plist_attribute = rfc5652.Attribute()
    plist_attribute["attrType"] = univ.ObjectIdentifier("1.2.840.113635.100.9.1")
    plist_attribute["attrValues"][0] = univ.Any(
        encoder.encode(
            univ.OctetString(plistlib.dumps({"cdhashes": expected_cdhashes}, fmt=plistlib.FMT_XML))
        )
    )
    full_value = signature_module._CDHashes2Value()
    full_value["algorithm"] = univ.ObjectIdentifier(
        "1.3.14.3.2.26" if wrong_algorithm else "2.16.840.1.101.3.4.2.1"
    )
    full_value["digest"] = (
        bytes(32) if wrong_full_digest else hashlib.sha256(alternate_directory.raw).digest()
    )
    full_attribute = rfc5652.Attribute()
    full_attribute["attrType"] = univ.ObjectIdentifier("1.2.840.113635.100.9.2")
    full_attribute["attrValues"][0] = univ.Any(encoder.encode(full_value))
    signer = signed_data["signerInfos"][0]
    signer["signedAttrs"].append(plist_attribute)
    signer["signedAttrs"].append(full_attribute)
    content_info["content"] = univ.Any(encoder.encode(signed_data)).subtype(
        explicitTag=content_info["content"].tagSet[-1]
    )
    return bytes(encoder.encode(content_info))


def superblob(directory: bytes, cms: bytes) -> bytes:
    wrapped_cms = struct.pack(">II", 0xFADE0B01, len(cms) + 8) + cms
    offset = 28
    length = offset + len(directory) + len(wrapped_cms)
    return b"".join(
        (
            struct.pack(">III", 0xFADE0CC0, length, 2),
            struct.pack(">II", 0, offset),
            struct.pack(">II", 0x10000, offset + len(directory)),
            directory,
            wrapped_cms,
        )
    )


def macho(
    identifier: str,
    key: rsa.RSAPrivateKey,
    cert: x509.Certificate,
    *,
    info: bytes | None = None,
    resources: bytes | None = None,
    adhoc: bool = False,
) -> bytes:
    signature_size = 4096
    for _ in range(3):
        signature_offset = 32 + 88
        header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 2, 88, 0, 0)
        segment = struct.pack(
            "<II16sQQQQIIII",
            0x19,
            72,
            b"__LINKEDIT",
            0,
            signature_size,
            signature_offset,
            signature_size,
            1,
            1,
            0,
            0,
        )
        command = struct.pack("<IIII", 0x1D, 16, signature_offset, signature_size)
        code = header + segment + command
        directory = code_directory(
            code,
            identifier,
            info=info,
            resources=resources,
            adhoc=adhoc,
        )
        signature = superblob(directory, cms_signature(directory, key, cert))
        if len(signature) == signature_size:
            return code + signature
        signature_size = len(signature)
    raise AssertionError("fixture signature size did not stabilize")


def resources(files: dict[str, bytes]) -> bytes:
    document = {
        "files": {},
        "files2": {
            path: {
                "hash": hashlib.sha1(content).digest(),
                "hash2": hashlib.sha256(content).digest(),
            }
            for path, content in files.items()
        },
        "rules": {"^.*": True},
        "rules2": {"^.*": True},
    }
    return plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True)


def signing_plan(certificate_sha256: str) -> SigningPlan:
    empty = normalize_entitlements({})
    root = SigningNodePlan(
        PurePosixPath("Payload/Fixture.app"),
        PurePosixPath("Payload/Fixture.app/Fixture"),
        BundleNodeKind.APP,
        1,
        "io.example.fixture",
        None,
        None,
        None,
        empty.values,
        empty.sha256,
    )
    nested = SigningNodePlan(
        PurePosixPath("Payload/Fixture.app/PlugIns/Ext.appex/Frameworks/Kit.dylib"),
        PurePosixPath("Payload/Fixture.app/PlugIns/Ext.appex/Frameworks/Kit.dylib"),
        BundleNodeKind.DYLIB,
        0,
        None,
        None,
        None,
        None,
        empty.values,
        empty.sha256,
    )
    return SigningPlan(
        "Fixture",
        "a" * 64,
        "b" * 64,
        certificate_sha256,
        SigningBackendIdentity("fixture", "1", "c" * 64, "1"),
        (nested, root),
        "d" * 64,
    )


def signed_ipa(
    tmp_path: Path,
    key: rsa.RSAPrivateKey,
    cert: x509.Certificate,
    *,
    tamper_nested: bool = False,
    extra_resource: bool = False,
    adhoc_root: bool = False,
) -> Path:
    nested = macho("Kit", key, cert)
    sealed_nested = nested
    if tamper_nested:
        nested = bytes([nested[0] ^ 1]) + nested[1:]
    relative_nested = "PlugIns/Ext.appex/Frameworks/Kit.dylib"
    resource_files = {relative_nested: sealed_nested}
    code_resources = resources(resource_files)
    info = plistlib.dumps(
        {
            "CFBundleIdentifier": "io.example.fixture",
            "CFBundleExecutable": "Fixture",
            "CFBundlePackageType": "APPL",
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )
    root = macho(
        "io.example.fixture",
        key,
        cert,
        info=info,
        resources=code_resources,
        adhoc=adhoc_root,
    )
    ipa = tmp_path / "signed.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr("Payload/Fixture.app/Info.plist", info)
        archive.writestr("Payload/Fixture.app/Fixture", root)
        archive.writestr("Payload/Fixture.app/_CodeSignature/CodeResources", code_resources)
        archive.writestr(f"Payload/Fixture.app/{relative_nested}", nested)
        if extra_resource:
            archive.writestr("Payload/Fixture.app/unsealed.txt", b"not sealed")
    return ipa


def test_verifies_every_planned_executable_and_parent_seal(tmp_path: Path) -> None:
    key, cert, digest = certificate()

    findings = verify_signed_signatures(signing_plan(digest), signed_ipa(tmp_path, key, cert))

    assert [(finding.node_path, finding.check, finding.passed) for finding in findings] == [
        (
            PurePosixPath("Payload/Fixture.app/PlugIns/Ext.appex/Frameworks/Kit.dylib"),
            "code-signature",
            True,
        ),
        (PurePosixPath("Payload/Fixture.app"), "code-signature", True),
        (PurePosixPath("Payload/Fixture.app"), "nested-resource-seal", True),
    ]


def test_nested_tamper_fails_both_child_signature_and_parent_seal(tmp_path: Path) -> None:
    key, cert, digest = certificate()

    findings = verify_signed_signatures(
        signing_plan(digest),
        signed_ipa(tmp_path, key, cert, tamper_nested=True),
    )

    failed = {(finding.node_path, finding.check) for finding in findings if not finding.passed}
    assert failed == {
        (
            PurePosixPath("Payload/Fixture.app/PlugIns/Ext.appex/Frameworks/Kit.dylib"),
            "code-signature",
        ),
        (PurePosixPath("Payload/Fixture.app"), "nested-resource-seal"),
    }


def test_rejects_unintended_identity_ad_hoc_and_unsealed_content(tmp_path: Path) -> None:
    key, cert, digest = certificate()
    _, _, other_digest = certificate()
    identity_findings = verify_signed_signatures(
        signing_plan(other_digest), signed_ipa(tmp_path, key, cert)
    )
    assert all(
        not finding.passed for finding in identity_findings if finding.check == "code-signature"
    )

    adhoc_findings = verify_signed_signatures(
        signing_plan(digest), signed_ipa(tmp_path, key, cert, adhoc_root=True)
    )
    root_signature = next(
        finding
        for finding in adhoc_findings
        if finding.node_path == PurePosixPath("Payload/Fixture.app")
        and finding.check == "code-signature"
    )
    assert not root_signature.passed
    assert "ad-hoc" in root_signature.diagnostics[0].message

    unsealed_findings = verify_signed_signatures(
        signing_plan(digest), signed_ipa(tmp_path, key, cert, extra_resource=True)
    )
    seal = next(finding for finding in unsealed_findings if finding.check == "nested-resource-seal")
    assert not seal.passed


def test_rejects_code_directory_identifier_drift(tmp_path: Path) -> None:
    key, cert, digest = certificate()
    plan = signing_plan(digest)
    root = next(node for node in plan.nodes if node.kind is BundleNodeKind.APP)
    plan = replace(
        plan,
        nodes=tuple(
            replace(node, target_bundle_id="io.other") if node == root else node
            for node in plan.nodes
        ),
    )

    findings = verify_signed_signatures(plan, signed_ipa(tmp_path, key, cert))

    root_signature = next(
        finding
        for finding in findings
        if finding.node_path == root.source_path and finding.check == "code-signature"
    )
    assert not root_signature.passed
    assert "identifier" in root_signature.diagnostics[0].message


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"short", "truncated"),
        (struct.pack(">III", 0, 12, 0), "bounds"),
        (
            struct.pack(">III", 0xFADE0CC0, 36, 2)
            + struct.pack(">II", 0, 28) * 2
            + struct.pack(">II", 0xFADE0C02, 8),
            "index",
        ),
        (
            struct.pack(">III", 0xFADE0CC0, 28, 1)
            + struct.pack(">II", 0, 20)
            + struct.pack(">II", 0xFADE0C02, 99),
            "blob bounds",
        ),
    ],
)
def test_rejects_malformed_superblobs(payload: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        signature_module._blob_map(payload)


@pytest.mark.parametrize(
    "payload, offset, message",
    [
        (b"value\0", 0, "offset"),
        (b"value", 1, "unterminated"),
        (b"a\xff\0", 1, "UTF-8"),
    ],
)
def test_rejects_malformed_code_directory_strings(
    payload: bytes, offset: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        signature_module._cstring(payload, offset, "fixture")


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value[:20], "truncated"),
        (
            lambda value: value[:4] + struct.pack(">I", len(value) + 1) + value[8:],
            "bounds",
        ),
        (lambda value: value[:37] + b"\xff" + value[38:], "hash algorithm"),
        (lambda value: value[:39] + b"\xff" + value[40:], "page size"),
        (
            lambda value: value[:16] + struct.pack(">I", len(value) + 1) + value[20:],
            "hash table",
        ),
    ],
)
def test_rejects_malformed_code_directories(mutation: object, message: str) -> None:
    code = bytes(120)
    valid = code_directory(code, "fixture")
    with pytest.raises(ValueError, match=message):
        signature_module._parse_code_directory(mutation(valid))  # type: ignore[operator]


def test_rejects_invalid_code_pages_and_special_slots(tmp_path: Path) -> None:
    code = bytes(120)
    parsed = signature_module._parse_code_directory(code_directory(code, "fixture"))
    with pytest.raises(ValueError, match="code limit"):
        signature_module._verify_code_directory(replace(parsed, code_limit=0), code, {}, None)
    with pytest.raises(ValueError, match="page hashes"):
        signature_module._verify_code_directory(
            replace(parsed, code_hashes=(bytes(32),)), bytes([1]) + code[1:], {}, None
        )

    info = b"info"
    resource_data = resources({})
    sealed = signature_module._parse_code_directory(
        code_directory(code, "fixture", info=info, resources=resource_data)
    )
    with pytest.raises(ValueError, match="external slot"):
        signature_module._verify_code_directory(sealed, code, {}, None)

    embedded_info = signature_module._parse_code_directory(
        code_directory(code, "fixture", info=info)
    )
    signature_module._verify_code_directory(
        embedded_info,
        code,
        {},
        None,
        info,
    )

    bundle = tmp_path / "Fixture.app"
    (bundle / "_CodeSignature").mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(b"wrong")
    (bundle / "_CodeSignature" / "CodeResources").write_bytes(resource_data)
    with pytest.raises(ValueError, match="special slot"):
        signature_module._verify_code_directory(sealed, code, {}, bundle)


def test_requires_cdhash_attributes_when_alternate_directory_exists() -> None:
    key, cert, _ = certificate()
    code = bytes(120)
    primary = signature_module._parse_code_directory(code_directory(code, "fixture"))
    cms = cms_signature(primary.raw, key, cert)

    signature_module._verify_cdhash_attributes(cms, primary, ())
    with pytest.raises(ValueError, match="hash attributes"):
        signature_module._verify_cdhash_attributes(cms, primary, (primary,))


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({}, None),
        ({"wrong_plist": True}, "plist does not match"),
        ({"wrong_algorithm": True}, "CDHashes2 attribute"),
        ({"wrong_full_digest": True}, "CDHashes2 digest"),
    ],
)
def test_verifies_apple_cdhash_attributes(mutation: dict[str, bool], message: str | None) -> None:
    key, cert, _ = certificate()
    code = bytes(120)
    primary = signature_module._parse_code_directory(code_directory(code, "fixture"))
    alternate = replace(primary, raw=primary.raw + b"alternate")
    cms = cms_with_cdhash_attributes(
        cms_signature(primary.raw, key, cert), primary, alternate, **mutation
    )

    if message is None:
        signature_module._verify_cdhash_attributes(cms, primary, (alternate,))
    else:
        with pytest.raises(ValueError, match=message):
            signature_module._verify_cdhash_attributes(cms, primary, (alternate,))


@pytest.mark.parametrize("mutation", ["missing-files2", "missing-hash", "wrong-hash"])
def test_rejects_malformed_resource_seals(tmp_path: Path, mutation: str) -> None:
    bundle = tmp_path / "Fixture.app"
    signature = bundle / "_CodeSignature"
    signature.mkdir(parents=True)
    executable = bundle / "Fixture"
    executable.write_bytes(b"executable")
    resource = bundle / "resource.txt"
    resource.write_bytes(b"resource")
    if mutation == "missing-files2":
        document: dict[str, object] = {}
    elif mutation == "missing-hash":
        document = {"files2": {"resource.txt": {}}}
    else:
        document = {"files2": {"resource.txt": {"hash2": bytes(32)}}}
    (signature / "CodeResources").write_bytes(plistlib.dumps(document))

    with pytest.raises(ValueError):
        signature_module._verify_resource_seal(bundle, executable)
