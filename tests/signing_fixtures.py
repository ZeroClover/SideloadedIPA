"""Signing inputs that real zsign accepts, shared by the unit and end-to-end suites.

Nothing here stands in for production code: the Mach-O is a genuine (minimal)
arm64 image and the identity is a genuine X.509 chain from a locally generated
authority. It is not issued by Apple and no device would trust it.
"""

from __future__ import annotations

import plistlib
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def unsigned_macho() -> bytes:
    """Arm64 MH_EXECUTE with an entitlement slot and replaceable signature command."""

    xml = plistlib.dumps({}, fmt=plistlib.FMT_XML, sort_keys=True)
    entitlement_blob = struct.pack(">II", 0xFADE7171, len(xml) + 8) + xml
    signature = b"".join(
        (
            struct.pack(">III", 0xFADE0CC0, 20 + len(entitlement_blob), 1),
            struct.pack(">II", 5, 20),
            entitlement_blob,
        )
    )
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


# zsign builds the CMS certificate chain from the intermediates compiled into it,
# selected by the leaf's issuer name, unless a P12 passed to it carries the chain.
# The pipeline hands zsign PEM key material, exactly as it does for an Apple
# certificate, so the fixture authority has to answer to a name zsign knows.
APPLE_WWDR_G3_NAME = x509.Name(
    [
        x509.NameAttribute(
            NameOID.COMMON_NAME, "Apple Worldwide Developer Relations Certification Authority"
        ),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "G3"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Apple Inc."),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    ]
)


@dataclass(frozen=True, slots=True)
class DevelopmentIdentity:
    key: rsa.RSAPrivateKey
    certificate: x509.Certificate
    issuer_certificate: x509.Certificate


def generate_development_identity(team_id: str = "TEAMID1234") -> DevelopmentIdentity:
    """Issue a code-signing leaf whose subject OU carries the team, as Apple's does."""

    issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer_name = APPLE_WWDR_G3_NAME
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Fixture Development Identity"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, team_id),
        ]
    )
    now = datetime.now(timezone.utc)
    issuer_certificate = (
        x509.CertificateBuilder()
        .subject_name(issuer_name)
        .issuer_name(issuer_name)
        .public_key(issuer_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )
    return DevelopmentIdentity(key, certificate, issuer_certificate)
