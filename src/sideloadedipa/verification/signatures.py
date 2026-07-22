"""Cryptographic Mach-O and bundle-seal verification on Linux."""

from __future__ import annotations

import hashlib
import plistlib
import struct
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import lief
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from pyasn1.codec.der import decoder
from pyasn1.error import PyAsn1Error
from pyasn1.type import namedtype, univ
from pyasn1_modules import rfc5652

from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
)
from sideloadedipa.errors import AdapterError
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.util.subprocesses import SubprocessRunner
from sideloadedipa.util.workspace import task_workspace

_SUPERBLOB_MAGIC = 0xFADE0CC0
_CODE_DIRECTORY_MAGIC = 0xFADE0C02
_BLOB_WRAPPER_MAGIC = 0xFADE0B01
_CODE_DIRECTORY_SLOT = 0
_ALTERNATE_CODE_DIRECTORY_FIRST = 0x1000
_ALTERNATE_CODE_DIRECTORY_LIMIT = 0x1005
_CMS_SLOT = 0x10000
_ADHOC_FLAG = 0x2
_INFO_SLOT = 1
_RESOURCE_SLOT = 3
_CDHASHES_PLIST_OID = "1.2.840.113635.100.9.1"
_CDHASHES2_OID = "1.2.840.113635.100.9.2"
_HASH_ALGORITHMS = {
    1: ("sha1", 20),
    2: ("sha256", 32),
    3: ("sha256", 20),
    4: ("sha384", 48),
}


class _CDHashes2Value(univ.Sequence):  # type: ignore[misc]
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("algorithm", univ.ObjectIdentifier()),
        namedtype.NamedType("digest", univ.OctetString()),
    )


@dataclass(frozen=True, slots=True)
class _CodeDirectory:
    raw: bytes
    flags: int
    identifier: str
    team_id: str | None
    code_limit: int
    page_size: int
    hash_name: str
    hash_size: int
    code_hashes: tuple[bytes, ...]
    special_hashes: tuple[tuple[int, bytes], ...]

    @property
    def cdhash(self) -> bytes:
        return hashlib.new(self.hash_name, self.raw).digest()[:20]


@dataclass(frozen=True, slots=True)
class _SliceSignature:
    architecture: str
    code_directory_sha256: str
    cdhash: bytes
    signer_certificate_sha256: str


def _blob_map(signature: bytes) -> dict[int, bytes]:
    if len(signature) < 12:
        raise ValueError("code signature SuperBlob is truncated")
    magic, length, count = struct.unpack_from(">III", signature)
    if magic != _SUPERBLOB_MAGIC or length > len(signature) or length < 12 + count * 8:
        raise ValueError("code signature SuperBlob bounds are invalid")
    blobs: dict[int, bytes] = {}
    for index in range(count):
        slot, offset = struct.unpack_from(">II", signature, 12 + index * 8)
        if slot in blobs or offset < 12 + count * 8 or offset + 8 > length:
            raise ValueError("code signature blob index is invalid")
        _, blob_length = struct.unpack_from(">II", signature, offset)
        if blob_length < 8 or offset + blob_length > length:
            raise ValueError("code signature blob bounds are invalid")
        blobs[slot] = signature[offset : offset + blob_length]
    return blobs


def _cstring(raw: bytes, offset: int, label: str) -> str:
    if offset <= 0 or offset >= len(raw):
        raise ValueError(f"CodeDirectory {label} offset is invalid")
    end = raw.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"CodeDirectory {label} is unterminated")
    try:
        return raw[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"CodeDirectory {label} is not UTF-8") from error


def _parse_code_directory(raw: bytes) -> _CodeDirectory:
    if len(raw) < 44:
        raise ValueError("CodeDirectory is truncated")
    (
        magic,
        length,
        version,
        flags,
        hash_offset,
        identifier_offset,
        special_count,
        code_count,
        code_limit,
    ) = struct.unpack_from(">9I", raw)
    hash_size, hash_type, _, page_size_log2 = struct.unpack_from(">4B", raw, 36)
    if magic != _CODE_DIRECTORY_MAGIC or length != len(raw):
        raise ValueError("CodeDirectory bounds are invalid")
    algorithm = _HASH_ALGORITHMS.get(hash_type)
    if algorithm is None or algorithm[1] != hash_size:
        raise ValueError("CodeDirectory uses an unsupported hash algorithm")
    if page_size_log2 > 30:
        raise ValueError("CodeDirectory page size is invalid")
    team_offset = struct.unpack_from(">I", raw, 48)[0] if version >= 0x20200 else 0
    code_limit_64 = struct.unpack_from(">Q", raw, 56)[0] if version >= 0x20300 else 0
    effective_limit = code_limit_64 or code_limit
    hashes_start = hash_offset - special_count * hash_size
    hashes_end = hash_offset + code_count * hash_size
    if hashes_start < 0 or hashes_end > len(raw):
        raise ValueError("CodeDirectory hash table bounds are invalid")
    special = tuple(
        (
            special_count - index,
            raw[hashes_start + index * hash_size : hashes_start + (index + 1) * hash_size],
        )
        for index in range(special_count)
    )
    code = tuple(
        raw[hash_offset + index * hash_size : hash_offset + (index + 1) * hash_size]
        for index in range(code_count)
    )
    return _CodeDirectory(
        raw,
        flags,
        _cstring(raw, identifier_offset, "identifier"),
        _cstring(raw, team_offset, "team") if team_offset else None,
        effective_limit,
        1 << page_size_log2,
        algorithm[0],
        hash_size,
        code,
        special,
    )


def _digest(directory: _CodeDirectory, payload: bytes) -> bytes:
    return hashlib.new(directory.hash_name, payload).digest()[: directory.hash_size]


def _verify_code_directory(
    directory: _CodeDirectory,
    slice_bytes: bytes,
    blobs: Mapping[int, bytes],
    bundle: Path | None,
    embedded_info_plist: bytes | None = None,
) -> None:
    if directory.flags & _ADHOC_FLAG:
        raise ValueError("CodeDirectory is ad-hoc signed")
    if directory.code_limit <= 0 or directory.code_limit > len(slice_bytes):
        raise ValueError("CodeDirectory code limit is invalid")
    expected_pages = tuple(
        _digest(
            directory, slice_bytes[offset : min(offset + directory.page_size, directory.code_limit)]
        )
        for offset in range(0, directory.code_limit, directory.page_size)
    )
    if expected_pages != directory.code_hashes:
        raise ValueError("CodeDirectory page hashes do not match the executable")

    external_paths = {
        _INFO_SLOT: bundle / "Info.plist" if bundle is not None else None,
        _RESOURCE_SLOT: bundle / "_CodeSignature" / "CodeResources" if bundle is not None else None,
    }
    for slot, expected in directory.special_hashes:
        if not any(expected):
            continue
        payload = blobs.get(slot)
        if payload is None and slot == _INFO_SLOT and bundle is None:
            payload = embedded_info_plist
        if payload is None and slot in external_paths:
            path = external_paths[slot]
            if path is None or not path.is_file():
                raise ValueError(f"CodeDirectory external slot {slot} is missing")
            payload = path.read_bytes()
        if payload is None or _digest(directory, payload) != expected:
            raise ValueError(f"CodeDirectory special slot {slot} does not match")


def _decode_cms(cms: bytes) -> rfc5652.SignedData:
    content_info, trailing = decoder.decode(cms, asn1Spec=rfc5652.ContentInfo())
    if trailing or str(content_info["contentType"]) != str(rfc5652.id_signedData):
        raise ValueError("CMS content is not a single SignedData value")
    signed_data, trailing = decoder.decode(content_info["content"], asn1Spec=rfc5652.SignedData())
    if trailing:
        raise ValueError("CMS SignedData has trailing content")
    return signed_data


def _signed_attribute_values(signed_data: rfc5652.SignedData, oid: str) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for signer in signed_data["signerInfos"]:
        for attribute in signer["signedAttrs"]:
            if str(attribute["attrType"]) == oid:
                values.extend(bytes(value) for value in attribute["attrValues"])
    return tuple(values)


def _verify_cdhash_attributes(
    cms: bytes,
    primary: _CodeDirectory,
    alternates: tuple[_CodeDirectory, ...],
) -> None:
    signed_data = _decode_cms(cms)
    if len(signed_data["signerInfos"]) != 1:
        raise ValueError("CMS must contain exactly one signer")
    plist_values = _signed_attribute_values(signed_data, _CDHASHES_PLIST_OID)
    full_values = _signed_attribute_values(signed_data, _CDHASHES2_OID)
    if not alternates and not plist_values and not full_values:
        return
    if len(plist_values) != 1 or len(full_values) != 1:
        raise ValueError("CMS is missing the Apple CodeDirectory hash attributes")
    plist_payload, trailing = decoder.decode(plist_values[0], asn1Spec=univ.OctetString())
    if trailing:
        raise ValueError("CMS cdhash plist attribute has trailing content")
    document = plistlib.loads(bytes(plist_payload))
    if not isinstance(document, Mapping) or not isinstance(document.get("cdhashes"), list):
        raise ValueError("CMS cdhash plist attribute is invalid")
    expected_cdhashes = [primary.cdhash, *(value.cdhash for value in alternates)]
    if document["cdhashes"] != expected_cdhashes:
        raise ValueError("CMS cdhash plist does not match the CodeDirectories")

    full_value, trailing = decoder.decode(full_values[0], asn1Spec=_CDHashes2Value())
    if trailing or str(full_value["algorithm"]) != "2.16.840.1.101.3.4.2.1":
        raise ValueError("CMS CDHashes2 attribute is invalid")
    selected = alternates[-1] if alternates else primary
    if bytes(full_value["digest"]) != hashlib.sha256(selected.raw).digest():
        raise ValueError("CMS CDHashes2 digest does not match the preferred CodeDirectory")


def _verify_cms(
    runner: SubprocessRunner,
    cms: bytes,
    primary: _CodeDirectory,
    expected_certificate_sha256: str,
    directory: Path,
) -> str:
    cms_path = directory / "signature.cms"
    content_path = directory / "CodeDirectory"
    signer_path = directory / "signer.pem"
    output_path = directory / "verified-content"
    cms_path.write_bytes(cms)
    content_path.write_bytes(primary.raw)
    try:
        runner.run(
            [
                "openssl",
                "cms",
                "-verify",
                "-verify_retcode",
                "-binary",
                "-inform",
                "DER",
                "-in",
                cms_path,
                "-content",
                content_path,
                "-noverify",
                "-signer",
                signer_path,
                "-out",
                output_path,
            ],
            timeout_seconds=30,
            path_redactions=(cms_path, content_path, signer_path, output_path),
        )
    except AdapterError as error:
        raise ValueError("CMS signature does not verify") from error
    certificates = x509.load_pem_x509_certificates(signer_path.read_bytes())
    if len(certificates) != 1:
        raise ValueError("CMS did not identify exactly one signer certificate")
    actual = hashlib.sha256(certificates[0].public_bytes(Encoding.DER)).hexdigest()
    if actual != expected_certificate_sha256:
        raise ValueError("CMS signer certificate does not match the signing plan")
    return actual


def _verify_executable(
    executable: Path,
    bundle: Path | None,
    *,
    expected_certificate_sha256: str,
    expected_identifier: str | None,
    runner: SubprocessRunner,
    temporary_root: Path,
) -> tuple[_SliceSignature, ...]:
    raw = executable.read_bytes()
    parsed = lief.MachO.parse(executable, config=lief.MachO.ParserConfig.quick)
    if parsed is None or parsed.size == 0:
        raise ValueError("executable is not a readable Mach-O file")
    evidence: list[_SliceSignature] = []
    for index, binary in enumerate(parsed):
        signature = binary.code_signature
        if signature is None:
            raise ValueError("Mach-O slice has no embedded code signature")
        start = binary.fat_offset
        end = start + binary.original_size
        signature_start = start + signature.data_offset
        signature_end = signature_start + signature.data_size
        if end > len(raw) or signature_end > end:
            raise ValueError("Mach-O slice or signature bounds are invalid")
        blobs = _blob_map(raw[signature_start:signature_end])
        primary_blob = blobs.get(_CODE_DIRECTORY_SLOT)
        cms_blob = blobs.get(_CMS_SLOT)
        if primary_blob is None or cms_blob is None:
            raise ValueError("Mach-O slice is missing CodeDirectory or CMS evidence")
        if struct.unpack_from(">I", cms_blob)[0] != _BLOB_WRAPPER_MAGIC or len(cms_blob) <= 8:
            raise ValueError("Mach-O CMS wrapper is invalid")
        primary = _parse_code_directory(primary_blob)
        alternates = tuple(
            _parse_code_directory(blobs[slot])
            for slot in sorted(blobs)
            if _ALTERNATE_CODE_DIRECTORY_FIRST <= slot < _ALTERNATE_CODE_DIRECTORY_LIMIT
        )
        directories = (primary, *alternates)
        embedded_info_plist = next(
            (
                bytes(section.content)
                for section in binary.sections
                if section.segment_name == "__TEXT" and section.name == "__info_plist"
            ),
            None,
        )
        for directory in directories:
            _verify_code_directory(
                directory,
                raw[start:end],
                blobs,
                bundle,
                embedded_info_plist,
            )
            if expected_identifier is not None and directory.identifier != expected_identifier:
                raise ValueError("CodeDirectory identifier does not match the signing plan")
        _verify_cdhash_attributes(cms_blob[8:], primary, alternates)
        slice_directory = temporary_root / f"slice-{index}"
        slice_directory.mkdir()
        signer_sha256 = _verify_cms(
            runner,
            cms_blob[8:],
            primary,
            expected_certificate_sha256,
            slice_directory,
        )
        evidence.append(
            _SliceSignature(
                binary.header.cpu_type.name,
                hashlib.sha256(primary.raw).hexdigest(),
                primary.cdhash,
                signer_sha256,
            )
        )
    return tuple(evidence)


def _bundle_for_node(root: Path, node: SigningNodePlan) -> Path | None:
    if node.kind in {BundleNodeKind.APP, BundleNodeKind.APP_EXTENSION, BundleNodeKind.FRAMEWORK}:
        return root.joinpath(*node.source_path.parts)
    return None


def _resource_files(bundle: Path, executable: Path) -> dict[str, Path]:
    excluded = {
        "Info.plist",
        "PkgInfo",
        "_CodeSignature/CodeResources",
        executable.relative_to(bundle).as_posix(),
    }
    return {
        path.relative_to(bundle).as_posix(): path
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path.relative_to(bundle).as_posix() not in excluded
    }


def _verify_resource_seal(bundle: Path, executable: Path) -> str:
    resource_path = bundle / "_CodeSignature" / "CodeResources"
    document = plistlib.loads(resource_path.read_bytes())
    if not isinstance(document, Mapping) or not isinstance(document.get("files2"), Mapping):
        raise ValueError("CodeResources has no files2 dictionary")
    entries = document["files2"]
    actual_files = _resource_files(bundle, executable)
    if set(entries) != set(actual_files):
        unsealed = sorted(set(actual_files) - set(entries))
        absent = sorted(set(entries) - set(actual_files))
        raise ValueError(
            "CodeResources file inventory does not match the bundle "
            f"(unsealed={unsealed[:3]!r}, absent={absent[:3]!r})"
        )
    for relative, path in actual_files.items():
        entry = entries[relative]
        if not isinstance(entry, Mapping) or not isinstance(entry.get("hash2"), bytes):
            raise ValueError(f"CodeResources entry has no SHA-256 seal: {relative}")
        if entry["hash2"] != hashlib.sha256(path.read_bytes()).digest():
            raise ValueError(f"CodeResources entry hash does not match bundle content: {relative}")
    return hashlib.sha256(resource_path.read_bytes()).hexdigest()


def _failure(
    plan: SigningPlan,
    node: SigningNodePlan,
    check: str,
    message: str,
) -> VerificationFinding:
    return VerificationFinding(
        node.source_path,
        check,
        False,
        plan.certificate_sha256 if check == "code-signature" else None,
        None,
        (
            Diagnostic(
                f"verification.{check.replace('-', '_')}",
                DiagnosticSeverity.ERROR,
                message,
                task_name=plan.task_name,
                bundle_id=node.target_bundle_id,
                remediation="discard the artifact, correct the signing input, and retry",
                details=(
                    ("node_path", node.source_path.as_posix()),
                    ("reason", message),
                ),
            ),
        ),
    )


def verify_signed_signatures(
    plan: SigningPlan,
    signed_ipa: Path,
    *,
    runner: SubprocessRunner | None = None,
) -> tuple[VerificationFinding, ...]:
    """Verify every planned Mach-O signature and every directory bundle resource seal."""

    command_runner = runner or SubprocessRunner(default_timeout_seconds=30)
    workspace_base = signed_ipa.parent / ".sideloadedipa-signature-verification"
    remove_workspace_base = not workspace_base.exists()
    try:
        with task_workspace(workspace_base, plan.task_name) as workspace:
            extract_ipa_safely(signed_ipa, workspace.extracted)
            findings: list[VerificationFinding] = []
            for node in plan.nodes:
                executable = workspace.extracted.joinpath(*node.executable_path.parts)
                bundle = _bundle_for_node(workspace.extracted, node)
                try:
                    with tempfile.TemporaryDirectory(
                        prefix="cms-", dir=workspace.root
                    ) as temporary:
                        slices = _verify_executable(
                            executable,
                            bundle,
                            expected_certificate_sha256=plan.certificate_sha256,
                            expected_identifier=node.target_bundle_id,
                            runner=command_runner,
                            temporary_root=Path(temporary),
                        )
                    evidence_digest = hashlib.sha256(
                        b"".join(
                            item.code_directory_sha256.encode()
                            + item.signer_certificate_sha256.encode()
                            for item in slices
                        )
                    ).hexdigest()
                    findings.append(
                        VerificationFinding(
                            node.source_path,
                            "code-signature",
                            bool(slices),
                            plan.certificate_sha256,
                            evidence_digest,
                        )
                    )
                except (OSError, ValueError, RuntimeError, struct.error, PyAsn1Error) as error:
                    findings.append(
                        _failure(plan, node, "code-signature", str(error) or type(error).__name__)
                    )
                    continue

                if bundle is None:
                    continue
                try:
                    seal_sha256 = _verify_resource_seal(bundle, executable)
                    findings.append(
                        VerificationFinding(
                            node.source_path,
                            "nested-resource-seal",
                            True,
                            seal_sha256,
                            seal_sha256,
                        )
                    )
                except (OSError, ValueError, plistlib.InvalidFileException) as error:
                    findings.append(
                        _failure(
                            plan,
                            node,
                            "nested-resource-seal",
                            str(error) or type(error).__name__,
                        )
                    )
            return tuple(findings)
    finally:
        if remove_workspace_base:
            try:
                workspace_base.rmdir()
            except OSError:
                pass
