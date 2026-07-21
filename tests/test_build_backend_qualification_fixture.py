"""Tests for the deterministic backend qualification IPA builder."""

from __future__ import annotations

import plistlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_backend_qualification_fixture import (
    SOURCE_EXECUTABLES,
    TARGETS,
    build_fixture,
    sha256_file,
)


def source_ipa(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for role, member in SOURCE_EXECUTABLES.items():
            archive.writestr(member, f"mach-o-{role}".encode())
    return path


def test_build_fixture_creates_deterministic_four_bundle_ipa(tmp_path: Path) -> None:
    source = source_ipa(tmp_path / "source.ipa")
    expected_source_sha256 = sha256_file(source)
    first = tmp_path / "first.ipa"
    second = tmp_path / "second.ipa"

    build_fixture(source, first, expected_source_sha256)
    build_fixture(source, second, expected_source_sha256)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert len(archive.namelist()) == 8
        for role, (bundle_path, executable, identifier, package_type) in TARGETS.items():
            plist = plistlib.loads(archive.read(f"{bundle_path}/Info.plist"))
            assert plist["CFBundleIdentifier"] == identifier
            assert plist["CFBundleExecutable"] == executable
            assert plist["CFBundlePackageType"] == package_type
            assert archive.read(f"{bundle_path}/{executable}") == f"mach-o-{role}".encode()


def test_build_fixture_rejects_unreviewed_source(tmp_path: Path) -> None:
    source = source_ipa(tmp_path / "source.ipa")

    with pytest.raises(ValueError, match="source IPA SHA-256"):
        build_fixture(source, tmp_path / "output.ipa", "0" * 64)
