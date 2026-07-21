"""Tests for authoritative root IPA metadata reads."""

from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path

import pytest

from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import read_ipa_metadata


def _ipa(path: Path, document: dict[str, object]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/Example.app/Info.plist", plistlib.dumps(document))
    return path


def test_reads_root_bundle_identifier_and_preferred_version(tmp_path: Path) -> None:
    path = _ipa(
        tmp_path / "Example.ipa",
        {
            "CFBundleIdentifier": "io.example.app",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "42",
        },
    )

    metadata = read_ipa_metadata(path)

    assert metadata.bundle_id == "io.example.app"
    assert metadata.version == "1.2.3"


@pytest.mark.parametrize(
    "document",
    [
        {"CFBundleVersion": "1"},
        {"CFBundleIdentifier": "io.example.app"},
    ],
)
def test_rejects_missing_publication_metadata(tmp_path: Path, document: dict[str, object]) -> None:
    with pytest.raises(DomainError) as caught:
        read_ipa_metadata(_ipa(tmp_path / "Invalid.ipa", document))

    assert caught.value.code is ErrorCode.SIGNING_VERIFICATION_FAILED


def test_rejects_invalid_ipa_archive(tmp_path: Path) -> None:
    path = tmp_path / "Invalid.ipa"
    path.write_bytes(b"not a zip archive")

    with pytest.raises(DomainError) as caught:
        read_ipa_metadata(path)

    assert caught.value.code is ErrorCode.SIGNING_VERIFICATION_FAILED
