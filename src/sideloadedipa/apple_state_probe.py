"""Read-only CI probe for the normalized Apple signing state contract."""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sideloadedipa.adapters.apple import AppleStateCollector, AscClient
from sideloadedipa.certificate_identity import (
    load_p12_certificate_identity,
    resolve_certificate_identity,
)
from sideloadedipa.domain import AppleStateSnapshot, CertificateIdentity
from sideloadedipa.errors import ConfigurationError, ErrorCode


def redacted_summary(snapshot: AppleStateSnapshot) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "counts": {
            "bundle_ids": len(snapshot.bundle_ids),
            "capabilities": len(snapshot.capabilities),
            "certificates": len(snapshot.certificates),
            "devices": len(snapshot.devices),
            "profiles": len(snapshot.profiles),
        },
    }


def redacted_certificate_summary(identity: CertificateIdentity) -> dict[str, str]:
    return {
        "resource_id": identity.resource_id,
        "serial_number": identity.serial_number,
        "public_key_sha256": identity.public_key_sha256,
        "certificate_sha256": identity.certificate_sha256,
        "expires_at": identity.expires_at.isoformat(),
    }


def _certificate_identity(snapshot: AppleStateSnapshot) -> CertificateIdentity | None:
    encoded = os.environ.get("APPLE_DEV_CERT_P12_ENCODED")
    password = os.environ.get("APPLE_DEV_CERT_PASSWORD")
    if not encoded and password is None:
        return None
    if not encoded or password is None:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            "certificate probe requires both P12 content and password",
        )
    try:
        p12_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "certificate probe P12 content is not valid base64",
        ) from error
    with tempfile.TemporaryDirectory(prefix="sideloadedipa-cert-") as directory:
        path = Path(directory) / "certificate.p12"
        path.write_bytes(p12_bytes)
        public_identity = load_p12_certificate_identity(path, password)
    return resolve_certificate_identity(
        snapshot=snapshot,
        identity=public_identity,
        now=datetime.now(timezone.utc),
    )


def main() -> int:
    snapshot = AppleStateCollector(AscClient()).collect()
    summary = redacted_summary(snapshot)
    identity = _certificate_identity(snapshot)
    if identity is not None:
        summary["certificate_identity"] = redacted_certificate_summary(identity)
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
