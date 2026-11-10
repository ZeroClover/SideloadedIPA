"""Fixtures for driving the installed ``sideloadedipa`` command end to end.

Every test runs the real console script in a child process against a scratch
workspace. Only the services the pipeline cannot reach from a test are replaced,
and only at a process or network boundary:

* Apple: a stand-in ``asc`` executable on PATH (``fake_asc.py``).
* The IPA host: a local HTTPS server trusted through ``SSL_CERT_FILE``.

Source intake, inventory, Apple reconciliation, patched zsign and independent
verification are all the production code paths.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from cryptography.x509.oid import NameOID

from e2e.harness import (
    CONSOLE_SCRIPT,
    DEVICE_UDID,
    FAKE_ASC_SOURCE,
    P12_PASSWORD,
    TEAM_ID,
    Apple,
    Outcome,
    Source,
    Tls,
    build_fixture_ipa,
    sha256_file,
    write_configuration,
)
from tests.signing_fixtures import DevelopmentIdentity, generate_development_identity


@pytest.fixture(scope="session")
def developer() -> DevelopmentIdentity:
    return generate_development_identity(TEAM_ID)


@pytest.fixture(scope="session")
def profile_signer() -> DevelopmentIdentity:
    return generate_development_identity("PROFILECA1")


@pytest.fixture(scope="session")
def tls(tmp_path_factory: pytest.TempPathFactory) -> Tls:
    """A private authority and a localhost server certificate it issued."""

    root = tmp_path_factory.mktemp("tls")
    now = datetime.now(timezone.utc)
    authority_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    authority_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "E2E Source Authority")])
    authority = (
        x509.CertificateBuilder()
        .subject_name(authority_name)
        .issuer_name(authority_name)
        .public_key(authority_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(authority_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(authority_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(authority_key, hashes.SHA256())
    )
    paths = Tls(root / "authority.pem", root / "server.pem", root / "server-key.pem")
    paths.authority_path.write_bytes(authority.public_bytes(Encoding.PEM))
    paths.certificate_path.write_bytes(server.public_bytes(Encoding.PEM))
    paths.key_path.write_bytes(
        server_key.private_bytes(
            Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return paths


@pytest.fixture
def source(tmp_path: Path, tls: Tls) -> Iterator[Source]:
    directory = tmp_path / "source"
    directory.mkdir()
    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            super().do_GET()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls.certificate_path, tls.key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Source(directory, f"https://localhost:{server.server_port}", requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def apple(
    tmp_path: Path, developer: DevelopmentIdentity, profile_signer: DevelopmentIdentity
) -> Apple:
    """An account holding the developer certificate and one enabled device."""

    root = tmp_path / "apple"
    (root / "bin").mkdir(parents=True)
    (root / "state").mkdir()
    executable = root / "bin" / "asc"
    executable.write_text(
        f"#!{sys.executable}\n{FAKE_ASC_SOURCE.read_text(encoding='utf-8')}", encoding="utf-8"
    )
    executable.chmod(0o755)
    (root / "state" / "profile-signer.pem").write_bytes(
        profile_signer.key.private_bytes(
            Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + profile_signer.certificate.public_bytes(Encoding.PEM)
    )
    certificate = developer.certificate
    state = {
        "team_id": TEAM_ID,
        "bundle_ids": [],
        "capabilities": [],
        "profiles": [],
        "certificates": [
            {
                "id": "CERT0001",
                "name": "Apple Development: E2E Fixture",
                "certificateType": "DEVELOPMENT",
                "displayName": "E2E Fixture",
                "serialNumber": format(certificate.serial_number, "X"),
                "platform": None,
                "expirationDate": certificate.not_valid_after_utc.isoformat(),
                "certificateContent": base64.b64encode(
                    certificate.public_bytes(Encoding.DER)
                ).decode(),
            }
        ],
        "devices": [
            {
                "id": "DEVICE0001",
                "name": "E2E iPhone",
                "platform": "IOS",
                "status": "ENABLED",
                "deviceClass": "IPHONE",
                "udid": DEVICE_UDID,
            }
        ],
    }
    (root / "state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return Apple(root)


@pytest.fixture(scope="session")
def zsign() -> dict[str, str]:
    """The patched backend, which a signing scenario may never run without."""

    configured = os.environ.get("ZSIGN_BIN")
    if not configured or not Path(configured).is_file():
        pytest.fail(
            "ZSIGN_BIN must name the patched zsign build; signing scenarios do not skip",
            pytrace=False,
        )
    return {
        "ZSIGN_BIN": configured,
        "ZSIGN_SHA256": os.environ.get("ZSIGN_SHA256") or sha256_file(Path(configured)),
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """The directory the command runs in, laid out like a repository checkout."""

    root = tmp_path / "workspace"
    (root / "configs").mkdir(parents=True)
    return root


@pytest.fixture
def cli(
    tmp_path: Path,
    workspace: Path,
    apple: Apple,
    tls: Tls,
    developer: DevelopmentIdentity,
) -> Callable[..., Outcome]:
    """Run ``sideloadedipa <args> --json`` in the workspace and capture the outcome."""

    home = tmp_path / "home"
    home.mkdir()
    p12 = pkcs12.serialize_key_and_certificates(
        b"E2E Fixture",
        developer.key,
        developer.certificate,
        [developer.issuer_certificate],
        serialization.BestAvailableEncryption(P12_PASSWORD.encode()),
    )
    environment = {
        "PATH": f"{apple.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "SSL_CERT_FILE": str(tls.authority_path),
        "APPLE_DEV_CERT_P12_ENCODED": base64.b64encode(p12).decode(),
        "APPLE_DEV_CERT_PASSWORD": P12_PASSWORD,
    }

    def run(*arguments: str, environment_overrides: dict[str, str] | None = None) -> Outcome:
        completed = subprocess.run(
            [str(CONSOLE_SCRIPT), *arguments, "--json"],
            cwd=workspace,
            env={**environment, **(environment_overrides or {})},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return Outcome(completed.returncode, completed.stdout, completed.stderr)

    return run


@pytest.fixture
def project(workspace: Path, source: Source) -> Path:
    """A workspace whose configured source is the standard fixture IPA."""

    build_fixture_ipa(source.directory / "Fixture.ipa")
    write_configuration(workspace, source)
    return workspace
