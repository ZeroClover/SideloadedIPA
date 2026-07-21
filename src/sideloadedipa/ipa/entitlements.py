"""Linux-compatible extraction of XML and DER Mach-O entitlement evidence."""

from __future__ import annotations

import plistlib
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lief
from pyasn1.codec.der import decoder
from pyasn1.error import PyAsn1Error
from pyasn1.type import char, namedtype, tag, univ

from sideloadedipa.errors import DomainError, ErrorCode

_EMBEDDED_SIGNATURE_MAGIC = 0xFADE0CC0
_XML_ENTITLEMENTS_MAGIC = 0xFADE7171
_DER_ENTITLEMENTS_MAGIC = 0xFADE7172
_XML_ENTITLEMENTS_SLOT = 5
_DER_ENTITLEMENTS_SLOT = 7


class _EntitlementPair(univ.Sequence):  # type: ignore[misc]
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("key", char.UTF8String()),
        namedtype.NamedType("value", univ.Any()),
    )


class _EntitlementDictionary(univ.SequenceOf):  # type: ignore[misc]
    componentType = _EntitlementPair()


_DICTIONARY_TAG = tag.Tag(tag.tagClassContext, tag.tagFormatConstructed, 16)


class _EntitlementDocument(univ.Sequence):  # type: ignore[misc]
    tagSet = univ.Sequence.tagSet.tagImplicitly(
        tag.Tag(tag.tagClassApplication, tag.tagFormatConstructed, 16)
    )
    componentType = namedtype.NamedTypes(
        namedtype.NamedType("version", univ.Integer()),
        namedtype.NamedType(
            "entries",
            _EntitlementDictionary().subtype(implicitTag=_DICTIONARY_TAG),
        ),
    )


@dataclass(frozen=True, slots=True)
class EntitlementSliceEvidence:
    index: int
    architecture: str
    xml_raw: bytes | None
    der_raw: bytes | None
    xml: Mapping[str, object] | None
    der: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class MachOEntitlementEvidence:
    slices: tuple[EntitlementSliceEvidence, ...]


def _error(message: str, path: Path, slice_index: int | None = None) -> DomainError:
    details: list[tuple[str, str | int]] = [("path", str(path))]
    if slice_index is not None:
        details.append(("slice_index", slice_index))
    return DomainError(
        ErrorCode.INVENTORY_ENTITLEMENTS_INVALID,
        message,
        remediation="replace the source IPA or repair its executable entitlement evidence",
        safe_details=tuple(details),
    )


def _decode(encoded: bytes, specification: Any) -> Any:
    value, trailing = decoder.decode(encoded, asn1Spec=specification)
    if trailing:
        raise PyAsn1Error("trailing DER data")
    return value


def _decode_der_dictionary(encoded: bytes) -> dict[str, object]:
    entries = _decode(
        encoded,
        _EntitlementDictionary().subtype(implicitTag=_DICTIONARY_TAG),
    )
    result: dict[str, object] = {}
    for pair in entries:
        key = str(pair["key"])
        if key in result:
            raise PyAsn1Error(f"duplicate entitlement key: {key}")
        result[key] = _decode_der_value(bytes(pair["value"]))
    return result


def _decode_der_value(encoded: bytes) -> object:
    if not encoded:
        raise PyAsn1Error("empty DER value")
    if encoded[0] == 0xB0:
        return _decode_der_dictionary(encoded)
    if encoded[0] == 0x30:
        values = _decode(encoded, univ.SequenceOf(componentType=univ.Any()))
        return [_decode_der_value(bytes(value)) for value in values]

    value = _decode(encoded, None)
    if isinstance(value, char.UTF8String):
        return str(value)
    if isinstance(value, univ.Boolean):
        return bool(value)
    if isinstance(value, univ.Integer):
        return int(value)
    if isinstance(value, univ.Real):
        return float(value)
    raise PyAsn1Error(f"unsupported entitlement DER type: {type(value).__name__}")


def decode_der_entitlements(payload: bytes) -> dict[str, object]:
    """Decode Apple's versioned DER entitlement dictionary."""

    document = _decode(payload, _EntitlementDocument())
    if int(document["version"]) != 1:
        raise PyAsn1Error("unsupported entitlement DER version")

    result: dict[str, object] = {}
    for pair in document["entries"]:
        key = str(pair["key"])
        if key in result:
            raise PyAsn1Error(f"duplicate entitlement key: {key}")
        result[key] = _decode_der_value(bytes(pair["value"]))
    return result


def _validate_plist_value(value: object) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for child in value:
            _validate_plist_value(child)
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("entitlement dictionary keys must be strings")
        for child in value.values():
            _validate_plist_value(child)
        return
    raise ValueError(f"unsupported entitlement plist type: {type(value).__name__}")


def _decode_xml_entitlements(payload: bytes) -> Mapping[str, object]:
    document = plistlib.loads(payload)
    if not isinstance(document, Mapping):
        raise ValueError("entitlement plist root must be a dictionary")
    _validate_plist_value(document)
    return document


def _entitlement_blobs(signature: bytes) -> tuple[bytes | None, bytes | None]:
    if len(signature) < 12:
        raise ValueError("code signature SuperBlob is truncated")
    magic, length, count = struct.unpack_from(">III", signature)
    if magic != _EMBEDDED_SIGNATURE_MAGIC:
        raise ValueError("code signature has an unexpected SuperBlob magic")
    if length < 12 or length > len(signature) or 12 + count * 8 > length:
        raise ValueError("code signature SuperBlob bounds are invalid")

    evidence: dict[int, bytes] = {}
    expected_magics = {
        _XML_ENTITLEMENTS_SLOT: _XML_ENTITLEMENTS_MAGIC,
        _DER_ENTITLEMENTS_SLOT: _DER_ENTITLEMENTS_MAGIC,
    }
    for index in range(count):
        slot, offset = struct.unpack_from(">II", signature, 12 + index * 8)
        if slot not in expected_magics:
            continue
        if slot in evidence:
            raise ValueError("code signature contains a duplicate entitlement slot")
        if offset < 12 + count * 8 or offset + 8 > length:
            raise ValueError("entitlement blob offset is outside the SuperBlob")
        blob_magic, blob_length = struct.unpack_from(">II", signature, offset)
        if blob_magic != expected_magics[slot]:
            raise ValueError("entitlement blob has an unexpected magic")
        if blob_length < 8 or offset + blob_length > length:
            raise ValueError("entitlement blob bounds are invalid")
        evidence[slot] = signature[offset + 8 : offset + blob_length]
    return evidence.get(_XML_ENTITLEMENTS_SLOT), evidence.get(_DER_ENTITLEMENTS_SLOT)


class LiefEntitlementInspector:
    """Read entitlement evidence without relying on macOS codesign."""

    def inspect(self, path: Path) -> MachOEntitlementEvidence:
        try:
            parsed = lief.MachO.parse(path, config=lief.MachO.ParserConfig.quick)
        except (OSError, RuntimeError) as error:
            raise _error("Mach-O executable could not be parsed", path) from error
        if parsed is None or parsed.size == 0:
            raise _error("Mach-O executable could not be parsed", path)

        slices: list[EntitlementSliceEvidence] = []
        try:
            with path.open("rb") as executable:
                for index, binary in enumerate(parsed):
                    signature = binary.code_signature
                    if signature is None:
                        raise _error("Mach-O slice has no embedded code signature", path, index)
                    executable.seek(binary.fat_offset + signature.data_offset)
                    signature_bytes = executable.read(signature.data_size)
                    if len(signature_bytes) != signature.data_size:
                        raise ValueError("code signature data is truncated")
                    xml_raw, der_raw = _entitlement_blobs(signature_bytes)
                    if xml_raw is None and der_raw is None:
                        raise ValueError("code signature has no entitlement evidence")
                    xml = _decode_xml_entitlements(xml_raw) if xml_raw is not None else None
                    der = decode_der_entitlements(der_raw) if der_raw is not None else None
                    slices.append(
                        EntitlementSliceEvidence(
                            index=index,
                            architecture=binary.header.cpu_type.name,
                            xml_raw=xml_raw,
                            der_raw=der_raw,
                            xml=xml,
                            der=der,
                        )
                    )
        except DomainError:
            raise
        except (OSError, ValueError, plistlib.InvalidFileException, PyAsn1Error) as error:
            slice_index = len(slices)
            raise _error(str(error), path, slice_index) from error
        return MachOEntitlementEvidence(tuple(slices))
