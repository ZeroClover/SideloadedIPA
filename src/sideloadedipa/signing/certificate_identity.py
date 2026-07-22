"""P12 public identity extraction and exact Apple certificate resolution."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    pkcs12,
)
from cryptography.x509.oid import NameOID

from sideloadedipa.domain import (
    AppleResourceKind,
    AppleResourceRequirement,
    AppleStateSnapshot,
    CertificateIdentity,
    CertificateMaterial,
    OperationDisposition,
    P12CertificateIdentity,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.util.atomics import atomic_write_bytes


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
    team_ids = certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
    if len(team_ids) != 1 or not team_ids[0].value:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configured P12 certificate does not contain exactly one Apple Team ID",
            remediation="use an Apple-issued code-signing certificate whose subject contains Team ID OU",
            safe_details=(("path_name", path.name),),
        )
    team_id = team_ids[0].value
    if not isinstance(team_id, str):
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configured P12 certificate contains a non-text Apple Team ID",
            remediation="use an Apple-issued code-signing certificate with a textual Team ID OU",
            safe_details=(("path_name", path.name),),
        )
    return P12CertificateIdentity(
        team_id=team_id,
        serial_number=format(certificate.serial_number, "X"),
        public_key_sha256=hashlib.sha256(public_key_der).hexdigest(),
        certificate_sha256=hashlib.sha256(certificate_der).hexdigest(),
        expires_at=certificate.not_valid_after_utc,
    )


def _atomic_private_write(path: Path, content: bytes) -> None:
    atomic_write_bytes(path, content)


def load_p12_certificate_material(
    path: Path,
    password: str,
    *,
    resource_id: str,
    output_directory: Path,
) -> CertificateMaterial:
    """Materialize a P12 as private PEM inputs for the qualified backend."""

    if not resource_id:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "certificate material requires a stable Apple resource ID",
        )
    public = load_p12_certificate_identity(path, password)
    try:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(
            path.read_bytes(), password.encode() if password else None
        )
    except (OSError, TypeError, ValueError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configured P12 could not be materialized",
            remediation="verify the P12 content and password",
            safe_details=(("path_name", path.name),),
        ) from error
    if private_key is None or certificate is None:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configured P12 must contain one private key and its certificate",
        )
    certificate_path = output_directory / "certificate.pem"
    private_key_path = output_directory / "private-key.pem"
    _atomic_private_write(certificate_path, certificate.public_bytes(Encoding.PEM))
    _atomic_private_write(
        private_key_path,
        private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
    )
    identity = CertificateIdentity(
        resource_id,
        public.team_id,
        public.serial_number,
        public.public_key_sha256,
        public.certificate_sha256,
        public.expires_at,
    )
    return CertificateMaterial(identity, certificate_path, private_key_path)


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
        team_id=identity.team_id,
        serial_number=identity.serial_number,
        public_key_sha256=identity.public_key_sha256,
        certificate_sha256=identity.certificate_sha256,
        expires_at=identity.expires_at,
    )
