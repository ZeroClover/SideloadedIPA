"""P12 public identity extraction and exact Apple certificate resolution."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12

from sideloadedipa.domain import (
    AppleResourceKind,
    AppleResourceRequirement,
    AppleStateSnapshot,
    CertificateIdentity,
    OperationDisposition,
    P12CertificateIdentity,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode


def load_p12_certificate_identity(path: Path, password: str) -> P12CertificateIdentity:
    """Extract only public certificate identity from a PKCS#12 container."""

    try:
        data = path.read_bytes()
    except OSError as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            "configured P12 could not be read",
            remediation="provide the configured development certificate P12",
            safe_details=(
                ("path_name", path.name),
                ("os_error", type(error).__name__),
            ),
        ) from error
    try:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(
            data, password.encode() if password else None
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configured P12 could not be decoded",
            remediation="verify the P12 content and password",
            safe_details=(("path_name", path.name),),
        ) from error
    if private_key is None or certificate is None:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configured P12 must contain one private key and its certificate",
            remediation="export the Apple development identity as a PKCS#12 file",
            safe_details=(("path_name", path.name),),
        )

    certificate_der = certificate.public_bytes(Encoding.DER)
    public_key_der = certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return P12CertificateIdentity(
        serial_number=format(certificate.serial_number, "X"),
        public_key_sha256=hashlib.sha256(public_key_der).hexdigest(),
        certificate_sha256=hashlib.sha256(certificate_der).hexdigest(),
        expires_at=certificate.not_valid_after_utc,
    )


def matching_certificate_resource_ids(
    snapshot: AppleStateSnapshot, identity: P12CertificateIdentity
) -> tuple[str, ...]:
    return tuple(
        sorted(
            value.resource_id
            for value in snapshot.certificates
            if value.certificate_sha256 == identity.certificate_sha256
        )
    )


def certificate_requirement(
    *, snapshot: AppleStateSnapshot, identity: P12CertificateIdentity
) -> AppleResourceRequirement:
    return AppleResourceRequirement(
        resource_kind=AppleResourceKind.CERTIFICATE,
        action="resolve-configured-development-certificate",
        target=identity.certificate_sha256,
        bundle_id=None,
        matching_resource_ids=matching_certificate_resource_ids(snapshot, identity),
        missing_disposition=OperationDisposition.BLOCKED,
        remediation=(
            "install a P12 whose certificate exactly matches one valid Apple development certificate"
        ),
    )


def resolve_certificate_identity(
    *,
    snapshot: AppleStateSnapshot,
    identity: P12CertificateIdentity,
    now: datetime,
) -> CertificateIdentity:
    if now.tzinfo is None:
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "certificate resolution requires a timezone-aware current time",
        )
    if identity.expires_at <= now.astimezone(timezone.utc):
        raise DomainError(
            ErrorCode.APPLE_RESOURCE_NOT_FOUND,
            "configured P12 certificate is expired",
            remediation="replace the P12 with a currently valid Apple development certificate",
            safe_details=(("expired_at", identity.expires_at.isoformat()),),
        )

    matches = tuple(
        value
        for value in snapshot.certificates
        if value.certificate_sha256 == identity.certificate_sha256
    )
    if len(matches) != 1:
        raise DomainError(
            (
                ErrorCode.APPLE_RESOURCE_NOT_FOUND
                if not matches
                else ErrorCode.APPLE_RESOURCE_CONFLICT
            ),
            "configured P12 must match exactly one Apple development certificate",
            remediation="verify the P12 and active development certificates without selecting by name",
            safe_details=(
                ("certificate_sha256", identity.certificate_sha256),
                ("matching_resource_ids", tuple(sorted(value.resource_id for value in matches))),
            ),
        )

    match = matches[0]
    if match.serial_number is not None and match.serial_number.upper() != identity.serial_number:
        raise DomainError(
            ErrorCode.ADAPTER_RESPONSE_INVALID,
            "Apple certificate serial number disagrees with the matched certificate content",
            remediation="refresh Apple certificate state before creating profiles",
            safe_details=(("resource_id", match.resource_id),),
        )
    return CertificateIdentity(
        resource_id=match.resource_id,
        serial_number=identity.serial_number,
        public_key_sha256=identity.public_key_sha256,
        certificate_sha256=identity.certificate_sha256,
        expires_at=identity.expires_at,
    )
