"""Tests for signed provisioning-profile decoding and authorization validation."""

from __future__ import annotations

import hashlib
import plistlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sideloadedipa.adapters.apple import MobileProvisionValidator
from sideloadedipa.domain import ProfileType, ProfileValidationRequest, normalize_entitlements
from sideloadedipa.errors import AdapterError, DomainError, ErrorCode
from sideloadedipa.profile_validation import (
    decode_and_validate_provisioning_profile,
    decode_mobileprovision,
    validate_provisioning_profile,
)

TEAM_ID = "TEAMID1234"
BUNDLE_ID = "io.example.app"
APPLICATION_IDENTIFIER = f"{TEAM_ID}.{BUNDLE_ID}"
NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
DEVICE_UDIDS = ("00008030-DEVICE-ONE", "00008030-DEVICE-TWO")


def make_certificate() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Profile Fixture")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(12345)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    der = certificate.public_bytes(serialization.Encoding.DER)
    return key, certificate, der


def profile_document(certificate_der: bytes) -> dict[str, object]:
    return {
        "Name": "Fixture Development Profile",
        "UUID": "FIXTURE-PROFILE-UUID",
        "TeamIdentifier": [TEAM_ID],
        "ApplicationIdentifierPrefix": [TEAM_ID],
        "CreationDate": datetime(2026, 7, 20, 12),
        "ExpirationDate": datetime(2026, 10, 20, 12),
        "DeveloperCertificates": [certificate_der],
        "ProvisionedDevices": list(DEVICE_UDIDS),
        "Entitlements": {
            "application-identifier": APPLICATION_IDENTIFIER,
            "com.apple.developer.team-identifier": TEAM_ID,
            "get-task-allow": True,
            "keychain-access-groups": [f"{TEAM_ID}.*"],
            "com.apple.security.application-groups": ["group.io.example.shared"],
        },
    }


def expected_entitlements() -> dict[str, object]:
    return {
        "application-identifier": APPLICATION_IDENTIFIER,
        "com.apple.developer.team-identifier": TEAM_ID,
        "get-task-allow": True,
        "keychain-access-groups": [
            f"{TEAM_ID}.{BUNDLE_ID}",
            f"{TEAM_ID}.shared.one",
        ],
        "com.apple.security.application-groups": ["group.io.example.shared"],
    }


def request(certificate_der: bytes) -> ProfileValidationRequest:
    return ProfileValidationRequest(
        resource_id="PROFILE_ONE",
        target_bundle_id=BUNDLE_ID,
        application_identifier=APPLICATION_IDENTIFIER,
        team_id=TEAM_ID,
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        certificate_sha256=hashlib.sha256(certificate_der).hexdigest(),
        device_udid_sha256=tuple(
            sorted(hashlib.sha256(value.encode()).hexdigest() for value in DEVICE_UDIDS)
        ),
        path=PurePosixPath("Task/profile.mobileprovision"),
        expected_entitlements=normalize_entitlements(expected_entitlements()).values,
    )


def sign_payload(
    tmp_path: Path,
    payload: bytes,
    key: ec.EllipticCurvePrivateKey,
    certificate: x509.Certificate,
) -> Path:
    payload_path = tmp_path / "profile.plist"
    key_path = tmp_path / "signer.key"
    certificate_path = tmp_path / "signer.pem"
    profile_path = tmp_path / "profile.mobileprovision"
    payload_path.write_bytes(payload)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    subprocess.run(
        [
            "openssl",
            "cms",
            "-sign",
            "-binary",
            "-nodetach",
            "-outform",
            "DER",
            "-in",
            str(payload_path),
            "-signer",
            str(certificate_path),
            "-inkey",
            str(key_path),
            "-out",
            str(profile_path),
        ],
        check=True,
        capture_output=True,
    )
    return profile_path


def test_verifies_cms_and_validates_complete_profile(tmp_path: Path) -> None:
    key, certificate, certificate_der = make_certificate()
    document = profile_document(certificate_der)
    path = sign_payload(tmp_path, plistlib.dumps(document), key, certificate)

    validated = decode_and_validate_provisioning_profile(
        path,
        request(certificate_der),
        now=NOW,
        refresh_threshold=timedelta(days=30),
    )
    validated_in_memory = MobileProvisionValidator(
        now=NOW,
        refresh_threshold=timedelta(days=30),
    ).validate(path.read_bytes(), request(certificate_der))

    assert validated.resource_id == "PROFILE_ONE"
    assert validated.application_identifier == APPLICATION_IDENTIFIER
    assert validated.device_ids == request(certificate_der).device_udid_sha256
    assert validated.profile_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert validated.expires_at == datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    assert validated_in_memory.profile_sha256 == validated.profile_sha256
    assert all(udid not in repr(validated) for udid in DEVICE_UDIDS)


def test_rejects_tampered_cms_and_non_plist_payload(tmp_path: Path) -> None:
    key, certificate, certificate_der = make_certificate()
    valid = sign_payload(
        tmp_path,
        plistlib.dumps(profile_document(certificate_der)),
        key,
        certificate,
    )
    damaged = bytearray(valid.read_bytes())
    damaged[-1] ^= 0x01
    valid.write_bytes(damaged)

    with pytest.raises(AdapterError) as signature_error:
        decode_mobileprovision(valid, bundle_id=BUNDLE_ID)
    assert signature_error.value.code is ErrorCode.APPLE_PROFILE_DECODE_FAILED
    assert str(valid) not in str(signature_error.value.safe_details)

    invalid_plist = sign_payload(tmp_path, b"not a plist", key, certificate)
    with pytest.raises(AdapterError) as plist_error:
        decode_mobileprovision(invalid_plist, bundle_id=BUNDLE_ID)
    assert plist_error.value.code is ErrorCode.APPLE_PROFILE_DECODE_FAILED

    list_payload = sign_payload(
        tmp_path, plistlib.dumps(["not", "a", "dictionary"]), key, certificate
    )
    with pytest.raises(AdapterError) as root_error:
        decode_mobileprovision(list_payload, bundle_id=BUNDLE_ID)
    assert root_error.value.code is ErrorCode.APPLE_PROFILE_DECODE_FAILED


def test_rejects_missing_profile_without_disclosing_path(tmp_path: Path) -> None:
    _, _, certificate_der = make_certificate()
    path = tmp_path / "private-task" / "missing.mobileprovision"

    with pytest.raises(AdapterError) as caught:
        decode_and_validate_provisioning_profile(
            path,
            request(certificate_der),
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )

    assert caught.value.code is ErrorCode.APPLE_PROFILE_DECODE_FAILED
    assert str(path) not in str(caught.value.safe_details)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(TeamIdentifier=["OTHERTEAM"]), "team"),
        (
            lambda value: value["Entitlements"].update(
                {"application-identifier": f"{TEAM_ID}.io.example.other"}
            ),
            "application identifier",
        ),
        (lambda value: value["Entitlements"].update({"get-task-allow": False}), "development"),
        (lambda value: value.update(DeveloperCertificates=[b"other"]), "certificate"),
        (lambda value: value.update(ProvisionedDevices=[DEVICE_UDIDS[0]]), "device"),
        (
            lambda value: value.update(ExpirationDate=datetime(2026, 8, 1, 12)),
            "refresh window",
        ),
    ],
)
def test_rejects_identity_type_certificate_device_and_date_mismatches(
    mutation: object, message: str
) -> None:
    _, _, certificate_der = make_certificate()
    document = profile_document(certificate_der)
    assert callable(mutation)
    mutation(document)

    with pytest.raises(DomainError) as caught:
        validate_provisioning_profile(
            document,
            b"profile",
            request(certificate_der),
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )

    assert caught.value.code is ErrorCode.APPLE_PROFILE_INVALID
    assert message in caught.value.message
    assert caught.value.bundle_id == BUNDLE_ID


def test_rejects_missing_entitlement_with_redacted_comparison_evidence() -> None:
    _, _, certificate_der = make_certificate()
    document = profile_document(certificate_der)
    entitlements = document["Entitlements"]
    assert isinstance(entitlements, dict)
    entitlements["com.apple.security.application-groups"] = ["group.io.example.other"]

    with pytest.raises(DomainError) as caught:
        validate_provisioning_profile(
            document,
            b"profile",
            request(certificate_der),
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )

    assert caught.value.code is ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED
    details = dict(caught.value.safe_details)
    assert details["key"] == "com.apple.security.application-groups"
    assert details["reason"] == "set-mismatch"
    assert len(details["expected_sha256"]) == 64
    assert len(details["actual_sha256"]) == 64
    assert "group.io.example" not in repr(caught.value.safe_details)


def test_app_group_authorization_requires_the_exact_expected_set() -> None:
    _, _, certificate_der = make_certificate()
    document = profile_document(certificate_der)
    entitlements = document["Entitlements"]
    assert isinstance(entitlements, dict)
    entitlements["com.apple.security.application-groups"] = [
        "group.io.example.shared",
        "group.io.example.unrequested",
    ]

    with pytest.raises(DomainError) as caught:
        validate_provisioning_profile(
            document,
            b"profile",
            request(certificate_der),
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )

    assert caught.value.code is ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED


def test_rejects_invalid_validation_clock_and_planned_identifier() -> None:
    _, _, certificate_der = make_certificate()
    document = profile_document(certificate_der)

    with pytest.raises(DomainError) as clock_error:
        validate_provisioning_profile(
            document,
            b"profile",
            request(certificate_der),
            now=NOW.replace(tzinfo=None),
            refresh_threshold=timedelta(days=30),
        )
    assert clock_error.value.code is ErrorCode.DOMAIN_INVARIANT

    original_request = request(certificate_der)
    invalid_request = ProfileValidationRequest(
        resource_id=original_request.resource_id,
        target_bundle_id=original_request.target_bundle_id,
        application_identifier=f"{TEAM_ID}.io.example.other",
        team_id=original_request.team_id,
        profile_type=original_request.profile_type,
        certificate_sha256=original_request.certificate_sha256,
        device_udid_sha256=original_request.device_udid_sha256,
        path=original_request.path,
        expected_entitlements=original_request.expected_entitlements,
    )
    entitlements = document["Entitlements"]
    assert isinstance(entitlements, dict)
    entitlements["application-identifier"] = invalid_request.application_identifier
    with pytest.raises(DomainError) as identifier_error:
        validate_provisioning_profile(
            document,
            b"profile",
            invalid_request,
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )
    assert identifier_error.value.code is ErrorCode.DOMAIN_INVARIANT


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(Name=""),
        lambda value: value.update(TeamIdentifier="not-an-array"),
        lambda value: value.update(TeamIdentifier=[1]),
        lambda value: value.update(Entitlements=[]),
        lambda value: value["Entitlements"].update(
            {"com.apple.developer.team-identifier": "OTHERTEAM"}
        ),
        lambda value: value.update(ApplicationIdentifierPrefix=["OTHERTEAM"]),
        lambda value: value.update(DeveloperCertificates=b"not-an-array"),
        lambda value: value.update(DeveloperCertificates=["not-data"]),
        lambda value: value.update(CreationDate="not-a-date"),
        lambda value: value.update(CreationDate=datetime(2026, 11, 1, 12)),
    ],
)
def test_rejects_malformed_profile_fields(mutation: object) -> None:
    _, _, certificate_der = make_certificate()
    document = profile_document(certificate_der)
    assert callable(mutation)
    mutation(document)

    with pytest.raises(DomainError) as caught:
        validate_provisioning_profile(
            document,
            b"profile",
            request(certificate_der),
            now=NOW,
            refresh_threshold=timedelta(days=30),
        )

    assert caught.value.code is ErrorCode.APPLE_PROFILE_INVALID


def test_entitlement_arrays_dicts_and_timezone_dates_use_authorization_semantics() -> None:
    _, _, certificate_der = make_certificate()
    document = profile_document(certificate_der)
    document["CreationDate"] = NOW - timedelta(days=1)
    document["ExpirationDate"] = NOW + timedelta(days=90)
    entitlements = document["Entitlements"]
    assert isinstance(entitlements, dict)
    entitlements.update(
        {
            "com.example.array": ["one", "two"],
            "com.example.dictionary": {"nested": True, "extra": "allowed"},
        }
    )
    expected = expected_entitlements()
    expected.update(
        {
            "com.example.array": ["two"],
            "com.example.dictionary": {"nested": True},
        }
    )
    original = request(certificate_der)
    extended_request = ProfileValidationRequest(
        resource_id=original.resource_id,
        target_bundle_id=original.target_bundle_id,
        application_identifier=original.application_identifier,
        team_id=original.team_id,
        profile_type=original.profile_type,
        certificate_sha256=original.certificate_sha256,
        device_udid_sha256=original.device_udid_sha256,
        path=original.path,
        expected_entitlements=normalize_entitlements(expected).values,
    )

    result = validate_provisioning_profile(
        document,
        b"profile",
        extended_request,
        now=NOW,
        refresh_threshold=timedelta(days=30),
    )

    assert result.created_at == NOW - timedelta(days=1)
