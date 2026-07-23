"""Tests for P12 identity extraction and exact Apple certificate matching."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

import sideloadedipa.apple.state_probe as state_probe
import sideloadedipa.signing.certificate_identity as certificate_identity_module
from sideloadedipa.apple.state_probe import (
    certificate_identity_from_environment,
    redacted_certificate_summary,
)
from sideloadedipa.domain import AppleCertificateState, AppleStateSnapshot
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.signing.certificate_identity import (
    certificate_requirement,
    load_p12_certificate_identity,
    load_p12_certificate_material,
    resolve_certificate_identity,
)


def make_p12(path: Path, password: str) -> tuple[bytes, datetime]:
    now = datetime.now(timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Fixture Development"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "TEAMID1234"),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(0x1234ABCD)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"fixture",
            key,
            certificate,
            None,
            serialization.BestAvailableEncryption(password.encode()),
        )
    )
    return certificate.public_bytes(serialization.Encoding.DER), certificate.not_valid_after_utc


def apple_certificate(
    resource_id: str, certificate_sha256: str, serial_number: str = "1234ABCD"
) -> AppleCertificateState:
    return AppleCertificateState(
        resource_id=resource_id,
        name="ignored display name",
        certificate_type="DEVELOPMENT",
        display_name="also ignored",
        serial_number=serial_number,
        platform="IOS",
        expiration_date=None,
        certificate_sha256=certificate_sha256,
    )


def snapshot(*certificates: AppleCertificateState) -> AppleStateSnapshot:
    return AppleStateSnapshot("digest", (), (), tuple(certificates), (), ())


def test_extracts_only_stable_public_identity(tmp_path: Path) -> None:
    path = tmp_path / "development.p12"
    certificate_der, expires_at = make_p12(path, "private-password")

    identity = load_p12_certificate_identity(path, "private-password")

    assert identity.serial_number == "1234ABCD"
    assert identity.team_id == "TEAMID1234"
    assert len(identity.public_key_sha256) == 64
    assert len(identity.certificate_sha256) == 64
    assert identity.expires_at == expires_at
    assert identity.certificate_sha256 == hashlib.sha256(certificate_der).hexdigest()


def test_materializes_private_backend_inputs_with_restricted_permissions(tmp_path: Path) -> None:
    path = tmp_path / "development.p12"
    make_p12(path, "private-password")

    material = load_p12_certificate_material(
        path,
        "private-password",
        resource_id="CERT_ONE",
        output_directory=tmp_path / "material",
    )

    assert material.identity.resource_id == "CERT_ONE"
    assert material.certificate_path.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    assert material.private_key_path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert material.certificate_path.stat().st_mode & 0o777 == 0o600
    assert material.private_key_path.stat().st_mode & 0o777 == 0o600


def test_materialization_decodes_pkcs12_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "development.p12"
    make_p12(path, "private-password")
    original = certificate_identity_module.pkcs12.load_key_and_certificates
    calls = 0

    def decode(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        certificate_identity_module.pkcs12,
        "load_key_and_certificates",
        decode,
    )

    load_p12_certificate_material(
        path,
        "private-password",
        resource_id="CERT_ONE",
        output_directory=tmp_path / "material",
    )

    assert calls == 1


def test_materialization_requires_stable_resource_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_p12_certificate_material(
            tmp_path / "unused.p12",
            "unused",
            resource_id="",
            output_directory=tmp_path / "material",
        )

    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_rejects_missing_or_bad_password_p12(tmp_path: Path) -> None:
    missing = tmp_path / "missing.p12"
    with pytest.raises(ConfigurationError) as missing_error:
        load_p12_certificate_identity(missing, "password")
    assert missing_error.value.code is ErrorCode.CONFIG_MISSING

    path = tmp_path / "development.p12"
    make_p12(path, "correct")
    with pytest.raises(ConfigurationError) as password_error:
        load_p12_certificate_identity(path, "wrong")
    assert password_error.value.code is ErrorCode.CONFIG_INVALID
    assert "wrong" not in str(password_error.value.safe_details)


def test_resolves_exact_single_certificate_without_using_display_name(tmp_path: Path) -> None:
    path = tmp_path / "development.p12"
    make_p12(path, "password")
    identity = load_p12_certificate_identity(path, "password")
    state = snapshot(apple_certificate("CERT_ONE", identity.certificate_sha256))

    resolved = resolve_certificate_identity(
        snapshot=state,
        identity=identity,
        now=datetime.now(timezone.utc),
    )
    requirement = certificate_requirement(snapshot=state, identity=identity)

    assert resolved.resource_id == "CERT_ONE"
    assert resolved.public_key_sha256 == identity.public_key_sha256
    assert requirement.matching_resource_ids == ("CERT_ONE",)
    assert redacted_certificate_summary(resolved) == {
        "resource_id": "CERT_ONE",
        "team_id": "TEAMID1234",
        "serial_number": identity.serial_number,
        "public_key_sha256": identity.public_key_sha256,
        "certificate_sha256": identity.certificate_sha256,
        "expires_at": identity.expires_at.isoformat(),
    }


def test_blocks_zero_duplicate_expired_and_conflicting_serial_matches(tmp_path: Path) -> None:
    path = tmp_path / "development.p12"
    make_p12(path, "password")
    identity = load_p12_certificate_identity(path, "password")
    now = datetime.now(timezone.utc)

    with pytest.raises(DomainError) as absent:
        resolve_certificate_identity(snapshot=snapshot(), identity=identity, now=now)
    assert absent.value.code is ErrorCode.APPLE_RESOURCE_NOT_FOUND

    duplicate_state = snapshot(
        apple_certificate("CERT_ONE", identity.certificate_sha256),
        apple_certificate("CERT_TWO", identity.certificate_sha256),
    )
    with pytest.raises(DomainError) as duplicate:
        resolve_certificate_identity(snapshot=duplicate_state, identity=identity, now=now)
    assert duplicate.value.code is ErrorCode.APPLE_RESOURCE_CONFLICT

    with pytest.raises(DomainError) as expired:
        resolve_certificate_identity(
            snapshot=snapshot(apple_certificate("CERT_ONE", identity.certificate_sha256)),
            identity=identity,
            now=identity.expires_at + timedelta(seconds=1),
        )
    assert expired.value.code is ErrorCode.APPLE_RESOURCE_NOT_FOUND

    with pytest.raises(DomainError) as serial:
        resolve_certificate_identity(
            snapshot=snapshot(
                apple_certificate("CERT_ONE", identity.certificate_sha256, "DIFFERENT")
            ),
            identity=identity,
            now=now,
        )
    assert serial.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID


def test_requires_timezone_aware_current_time(tmp_path: Path) -> None:
    path = tmp_path / "development.p12"
    make_p12(path, "password")
    identity = load_p12_certificate_identity(path, "password")

    with pytest.raises(DomainError) as caught:
        resolve_certificate_identity(snapshot=snapshot(), identity=identity, now=datetime.now())

    assert caught.value.code is ErrorCode.DOMAIN_INVARIANT


def test_certificate_probe_validates_environment_and_resolves_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APPLE_DEV_CERT_P12_ENCODED", raising=False)
    monkeypatch.delenv("APPLE_DEV_CERT_PASSWORD", raising=False)
    assert certificate_identity_from_environment(snapshot()) is None

    monkeypatch.setenv("APPLE_DEV_CERT_P12_ENCODED", "not-base64")
    with pytest.raises(ConfigurationError) as missing_password:
        certificate_identity_from_environment(snapshot())
    assert missing_password.value.code is ErrorCode.CONFIG_MISSING

    monkeypatch.setenv("APPLE_DEV_CERT_PASSWORD", "password")
    with pytest.raises(ConfigurationError) as invalid_content:
        certificate_identity_from_environment(snapshot())
    assert invalid_content.value.code is ErrorCode.CONFIG_INVALID

    path = tmp_path / "development.p12"
    make_p12(path, "password")
    identity = load_p12_certificate_identity(path, "password")
    monkeypatch.setenv("APPLE_DEV_CERT_P12_ENCODED", base64.b64encode(path.read_bytes()).decode())

    resolved = certificate_identity_from_environment(
        snapshot(apple_certificate("CERT_ONE", identity.certificate_sha256))
    )

    assert resolved is not None
    assert resolved.resource_id == "CERT_ONE"


def test_state_probe_main_prints_only_redacted_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    current = snapshot(apple_certificate("CERT_ONE", "c" * 64))
    identity = state_probe.CertificateIdentity(
        resource_id="CERT_ONE",
        team_id="TEAMID1234",
        serial_number="1234ABCD",
        public_key_sha256="a" * 64,
        certificate_sha256="c" * 64,
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )

    class Collector:
        def __init__(self, client: object) -> None:
            assert client == "client"

        def collect(self) -> AppleStateSnapshot:
            return current

    monkeypatch.setattr(state_probe, "AscClient", lambda: "client")
    monkeypatch.setattr(state_probe, "AppleStateCollector", Collector)
    monkeypatch.setattr(
        state_probe,
        "certificate_identity_from_environment",
        lambda value: identity,
    )

    assert state_probe.main() == 0
    document = json.loads(capsys.readouterr().out)
    assert document["snapshot_sha256"] == "digest"
    assert document["certificate_identity"]["resource_id"] == "CERT_ONE"
    assert "private" not in json.dumps(document)
