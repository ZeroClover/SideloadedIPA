"""Tests for the upstream zsign backend qualification exercise."""

from __future__ import annotations

import hashlib
import os
import plistlib
import struct
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7, pkcs12
from cryptography.x509.oid import NameOID

from sideloadedipa.adapters.signing.zsign import ZsignBackend
from sideloadedipa.domain import (
    BundleNodeKind,
    CertificateIdentity,
    CertificateMaterial,
    SigningNodePlan,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.tools.exercise_codesign_oracle import signing_order as oracle_signing_order
from sideloadedipa.tools.exercise_zsign_backend import (
    TARGETS,
    BackendExerciseError,
    configured_entitlements,
    evaluate_contract,
    inspect_entitlements,
    keychain_groups,
    materialize_entitlements,
    profile_resource_seal_matches,
    redacted_output,
    signing_order,
    zsign_command,
)
from sideloadedipa.verification import inspect_signed_entitlements, verify_signed_signatures

HELPER = PurePosixPath("Payload/Qualification.app/Frameworks/helper")


def _unsigned_macho() -> bytes:
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


def _generated_signing_material(
    root: Path,
) -> tuple[Path, Path, x509.Certificate, rsa.RSAPrivateKey]:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fixture Issuing CA")])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "zsign integration fixture"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "TEAMID1234"),
        ]
    )
    now = datetime.now(timezone.utc)
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    private_key = root / "private-key.p12"
    certificate_path = root / "certificate.pem"
    private_key.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"zsign integration fixture",
            key,
            certificate,
            [ca_certificate],
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(Encoding.PEM))
    return private_key, certificate_path, certificate, key


def _profile(
    certificate: x509.Certificate,
    key: rsa.RSAPrivateKey,
    bundle_identifier: str,
) -> tuple[bytes, dict[str, object]]:
    entitlements: dict[str, object] = {
        "application-identifier": f"TEAMID1234.{bundle_identifier}",
        "com.apple.developer.team-identifier": "TEAMID1234",
        "com.apple.security.application-groups": ["group.example"],
        "get-task-allow": True,
        "keychain-access-groups": ["TEAMID1234.*"],
    }
    if bundle_identifier.endswith(("livecontainer", "LiveProcess")):
        entitlements.update(
            {
                "com.apple.developer.healthkit": True,
                "com.apple.developer.healthkit.access": ["health-records"],
                "com.apple.developer.healthkit.background-delivery": True,
                "com.apple.developer.kernel.increased-memory-limit": True,
            }
        )
    content = plistlib.dumps(
        {
            "UUID": f"fixture-{bundle_identifier}",
            "Name": f"Fixture {bundle_identifier}",
            "TeamIdentifier": ["TEAMID1234"],
            "ApplicationIdentifierPrefix": ["TEAMID1234"],
            "CreationDate": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "ExpirationDate": datetime(2027, 1, 1, tzinfo=timezone.utc),
            "DeveloperCertificates": [certificate.public_bytes(Encoding.DER)],
            "ProvisionedDevices": ["fixture-device"],
            "Entitlements": entitlements,
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )
    signed = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(content)
        .add_signer(certificate, key, hashes.SHA256())
        .sign(Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )
    return signed, entitlements


def _fixture_ipa(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for _, (bundle, executable, identifier) in TARGETS.items():
            archive.writestr(
                f"{bundle}/Info.plist",
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": identifier,
                        "CFBundleExecutable": executable,
                        "CFBundlePackageType": "APPL" if bundle.endswith(".app") else "XPC!",
                        "CFBundleVersion": "1",
                    },
                    sort_keys=True,
                ),
            )
            archive.writestr(f"{bundle}/{executable}", _unsigned_macho())
        archive.writestr(HELPER.as_posix(), _unsigned_macho())


def profile_entitlements(bundle_identifier: str) -> dict:
    return {
        "application-identifier": f"TEAM.{bundle_identifier}",
        "com.apple.security.application-groups": ["group.example"],
        "get-task-allow": True,
        "keychain-access-groups": [f"TEAM.{bundle_identifier}", "TEAM.*"],
    }


def entitlement_contract(keychain_groups: list[str]) -> dict[str, dict]:
    common = {
        "com.apple.security.application-groups": ["group.example"],
        "get-task-allow": True,
        "keychain-access-groups": ["TEAM.*"],
    }
    result = {role: dict(common) for role in TARGETS}
    for role in ("root", "process"):
        bundle_identifier = TARGETS[role][2]
        result[role].update(
            {
                "application-identifier": f"TEAM.{bundle_identifier}",
                "com.apple.developer.healthkit": True,
                "com.apple.developer.healthkit.access": ["health-records"],
                "com.apple.developer.healthkit.background-delivery": True,
                "com.apple.developer.kernel.increased-memory-limit": True,
                "keychain-access-groups": keychain_groups,
            }
        )
    return result


def test_root_oracle_materializes_exact_128_authorized_groups() -> None:
    bundle_identifier = "io.zeroclover.app.livecontainer"

    result = materialize_entitlements(
        "root", bundle_identifier, profile_entitlements(bundle_identifier)
    )

    assert result["keychain-access-groups"] == keychain_groups(
        f"TEAM.{bundle_identifier}", bundle_identifier
    )
    assert len(result["keychain-access-groups"]) == 128
    assert result["keychain-access-groups"][0] == "TEAM.com.kdt.livecontainer.shared"
    assert result["keychain-access-groups"][-1].endswith(".127")


def test_extension_oracle_preserves_profile_entitlements() -> None:
    bundle_identifier = "io.zeroclover.app.livecontainer.ShareExtension"
    source = profile_entitlements(bundle_identifier)

    result = materialize_entitlements("share", bundle_identifier, source)

    assert result == source
    assert result is not source


def test_oracle_rejects_unauthorized_keychain_groups() -> None:
    bundle_identifier = "io.zeroclover.app.livecontainer.LiveProcess"
    source = profile_entitlements(bundle_identifier)
    source["keychain-access-groups"] = [f"TEAM.{bundle_identifier}"]

    with pytest.raises(BackendExerciseError, match="does not authorize 128"):
        materialize_entitlements("process", bundle_identifier, source)


def test_oracle_signing_order_is_deterministic_and_root_last() -> None:
    assert oracle_signing_order() == ["launch", "process", "share", "root"]


def test_zsign_command_uses_four_profiles_without_global_entitlements(tmp_path: Path) -> None:
    command = zsign_command(
        tmp_path / "zsign",
        tmp_path / "private-key.pem",
        tmp_path / "certificate.pem",
        tmp_path / "profiles",
        tmp_path / "fixture.ipa",
        tmp_path / "signed.ipa",
    )

    assert command.count("-m") == 4
    assert "-e" not in command
    assert "-p" not in command


def test_zsign_command_pairs_each_profile_with_its_entitlements(tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    entitlements_dir = tmp_path / "entitlements"

    command = zsign_command(
        tmp_path / "zsign",
        tmp_path / "private-key.pem",
        tmp_path / "certificate.pem",
        profiles_dir,
        tmp_path / "fixture.ipa",
        tmp_path / "signed.ipa",
        entitlements_dir,
    )

    pairs = [command[index : index + 4] for index, value in enumerate(command) if value == "-m"]
    assert pairs == [
        [
            "-m",
            str(profiles_dir / f"{role}.mobileprovision"),
            "-e",
            str(entitlements_dir / f"{role}.plist"),
        ]
        for role in TARGETS
    ]


def test_backend_output_is_bounded_and_redacted() -> None:
    output = f"prefix secret {'x' * 3000}"

    result = redacted_output(output, ["secret"])

    assert "secret" not in result
    assert len(result) == 2000


def test_profile_resource_seal_requires_matching_sha256_entry(tmp_path: Path) -> None:
    bundle = tmp_path / "Fixture.app"
    signature = bundle / "_CodeSignature"
    signature.mkdir(parents=True)
    profile = b"profile"
    document = {"files2": {"embedded.mobileprovision": {"hash2": hashlib.sha256(profile).digest()}}}
    (signature / "CodeResources").write_bytes(plistlib.dumps(document))

    assert profile_resource_seal_matches(bundle, profile)
    assert not profile_resource_seal_matches(bundle, b"changed")


def test_signing_order_is_parsed_from_backend_evidence() -> None:
    output = """
>>> SignFolder: PlugIns/LaunchAppExtension.appex, (LaunchAppExtension)
>>> SignFolder: PlugIns/LiveProcess.appex, (LiveProcess)
>>> SignFolder: PlugIns/ShareExtension.appex, (ShareExtension)
>>> SignFolder: Qualification.app, (Qualification)
"""

    assert signing_order(output) == ["launch", "process", "share", "root"]


def test_contract_rejects_profile_wildcard_instead_of_128_exact_groups() -> None:
    violations = evaluate_contract(entitlement_contract(["TEAM.*"]))

    assert violations == [
        "root does not contain the exact 128 keychain groups",
        "process does not contain the exact 128 keychain groups",
    ]


def test_contract_accepts_distinct_root_and_extension_entitlements() -> None:
    base = "TEAM.com.kdt.livecontainer.shared"
    keychain_groups = [base, *(f"{base}.{index}" for index in range(1, 128))]

    assert evaluate_contract(entitlement_contract(keychain_groups)) == []


def test_canary_materializes_production_template_from_profile_authorization() -> None:
    target = TARGETS["process"][2]
    profile = {
        "application-identifier": f"TEAM.{target}",
        "com.apple.developer.team-identifier": "TEAM",
        "com.apple.security.application-groups": ["group.io.zeroclover.app.livecontainer"],
        "com.apple.developer.healthkit": True,
        "com.apple.developer.healthkit.access": ["health-records"],
        "com.apple.developer.healthkit.background-delivery": True,
        "com.apple.developer.kernel.increased-memory-limit": True,
        "get-task-allow": True,
        "keychain-access-groups": ["TEAM.*"],
    }

    values = configured_entitlements(Path("configs/tasks.toml"), "process", profile)

    assert values["application-identifier"] == f"TEAM.{target}"
    assert values["com.apple.security.application-groups"] == [
        "group.io.zeroclover.app.livecontainer"
    ]
    groups = values["keychain-access-groups"]
    assert isinstance(groups, list)
    assert len(groups) == 128
    assert groups[0] == "TEAM.com.kdt.livecontainer.shared"
    assert groups[-1] == "TEAM.com.kdt.livecontainer.shared.127"


def test_canary_uses_profile_policy_for_launch_extension() -> None:
    target = TARGETS["launch"][2]
    profile = {
        "application-identifier": f"TEAM.{target}",
        "com.apple.developer.team-identifier": "TEAM",
        "com.apple.security.application-groups": ["group.io.zeroclover.app.livecontainer"],
        "get-task-allow": True,
    }

    assert configured_entitlements(Path("configs/tasks.toml"), "launch", profile) == profile


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("ZSIGN_BIN"), reason="set ZSIGN_BIN to patched zsign")
def test_real_patched_zsign_pairs_generated_profiles_and_entitlements(tmp_path: Path) -> None:
    zsign = Path(os.environ["ZSIGN_BIN"])
    private_key, certificate_path, certificate, key = _generated_signing_material(tmp_path)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile_bytes: dict[str, bytes] = {}
    expected: dict[str, dict[str, object]] = {}
    for role, (_, _, bundle_identifier) in TARGETS.items():
        content, authorized = _profile(certificate, key, bundle_identifier)
        profile_bytes[role] = content
        expected[role] = materialize_entitlements(role, bundle_identifier, authorized)
        (profiles / f"{role}.mobileprovision").write_bytes(content)

    fixture = tmp_path / "fixture.ipa"
    signed = tmp_path / "signed.ipa"
    _fixture_ipa(fixture)
    adapter = ZsignBackend(
        executable=zsign,
        expected_executable_sha256=hashlib.sha256(zsign.read_bytes()).hexdigest(),
        profile_root=profiles,
    )
    identity = adapter.identity()
    certificate_der = certificate.public_bytes(Encoding.DER)
    certificate_sha256 = hashlib.sha256(certificate_der).hexdigest()
    material = CertificateMaterial(
        identity=CertificateIdentity(
            resource_id="FIXTURE_CERTIFICATE",
            team_id="TEAMID1234",
            serial_number=format(certificate.serial_number, "X"),
            public_key_sha256=hashlib.sha256(
                certificate.public_key().public_bytes(
                    Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            ).hexdigest(),
            certificate_sha256=certificate_sha256,
            expires_at=certificate.not_valid_after_utc,
        ),
        certificate_path=certificate_path,
        private_key_path=private_key,
    )
    empty_entitlements = normalize_entitlements({})
    nodes = [
        SigningNodePlan(
            source_path=HELPER,
            executable_path=HELPER,
            kind=BundleNodeKind.EXECUTABLE,
            order=0,
            target_bundle_id=None,
            profile_resource_id=None,
            profile_path=None,
            profile_sha256=None,
            expected_entitlements=empty_entitlements.values,
            expected_entitlements_sha256=empty_entitlements.sha256,
        )
    ]
    for order, role in enumerate(("launch", "process", "share", "root"), start=1):
        bundle, executable, bundle_identifier = TARGETS[role]
        entitlements = normalize_entitlements(expected[role])
        profile = profile_bytes[role]
        nodes.append(
            SigningNodePlan(
                source_path=PurePosixPath(bundle),
                executable_path=PurePosixPath(bundle) / executable,
                kind=(BundleNodeKind.APP if role == "root" else BundleNodeKind.APP_EXTENSION),
                order=order,
                target_bundle_id=bundle_identifier,
                profile_resource_id=f"FIXTURE_{role.upper()}",
                profile_path=PurePosixPath(f"{role}.mobileprovision"),
                profile_sha256=hashlib.sha256(profile).hexdigest(),
                expected_entitlements=entitlements.values,
                expected_entitlements_sha256=entitlements.sha256,
            )
        )
    plan = SigningPlan(
        task_name="zsign-integration-fixture",
        source_ipa_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
        graph_sha256="0" * 64,
        certificate_sha256=certificate_sha256,
        backend=identity,
        nodes=tuple(nodes),
        plan_sha256="1" * 64,
    )

    result = adapter.sign(plan, fixture, signed, material)

    assert result.backend == identity
    assert result.backend_argv.count("-m") == len(TARGETS)
    assert result.backend_argv.count("-e") == len(TARGETS)
    for index, value in enumerate(result.backend_argv):
        if value == "-m":
            assert result.backend_argv[index + 2] == "-e"

    extracted = tmp_path / "signed"
    with zipfile.ZipFile(signed) as archive:
        archive.extractall(extracted)
    actual: dict[str, dict[str, object]] = {}
    for role, (bundle, executable, _) in TARGETS.items():
        bundle_path = extracted / bundle
        assert (bundle_path / "embedded.mobileprovision").read_bytes() == profile_bytes[role]
        assert profile_resource_seal_matches(bundle_path, profile_bytes[role])
        actual[role] = inspect_entitlements(
            zsign,
            bundle_path / executable,
            tmp_path / "debug" / role,
        )
        assert actual[role] == expected[role]
    assert evaluate_contract(actual) == []
    helper_evidence = inspect_signed_entitlements(
        plan,
        extracted,
        hashlib.sha256(signed.read_bytes()).hexdigest(),
    ).nodes[0]
    assert helper_evidence.source_path == HELPER
    assert all(value.xml is None and value.der is None for value in helper_evidence.slices)
    assert {node.source_path for node in result.nodes} == {
        HELPER,
        *(PurePosixPath(bundle) for bundle, _, _ in TARGETS.values()),
    }
    assert all(finding.passed for finding in verify_signed_signatures(plan, extracted))

    tampered = tmp_path / "tampered.ipa"
    with zipfile.ZipFile(signed) as source, zipfile.ZipFile(tampered, "w") as destination:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == HELPER.as_posix():
                content = bytes((content[0] ^ 1,)) + content[1:]
            destination.writestr(info, content)
    tampered_root = tmp_path / "tampered"
    with zipfile.ZipFile(tampered) as archive:
        archive.extractall(tampered_root)
    assert any(not finding.passed for finding in verify_signed_signatures(plan, tampered_root))
