"""Tests for exactly-one root application discovery."""

from __future__ import annotations

import hashlib
import plistlib
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import BundleNodeKind
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import discover_root_app


def make_app(
    root: Path,
    name: str = "App.app",
    *,
    overrides: dict[str, object] | None = None,
    plist_value: object | None = None,
) -> Path:
    bundle = root / "Payload" / name
    bundle.mkdir(parents=True)
    document: object = {
        "CFBundlePackageType": "APPL",
        "CFBundleIdentifier": "com.example.app",
        "CFBundleExecutable": "App",
        "CFBundleVersion": "123",
        "CFBundleShortVersionString": "1.2.3",
    }
    if overrides is not None:
        assert isinstance(document, dict)
        document.update(overrides)
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps(document if plist_value is None else plist_value)
    )
    (bundle / "App").write_bytes(b"Mach-O executable")
    return bundle


def test_discovers_valid_root_and_records_metadata_hashes(tmp_path: Path) -> None:
    bundle = make_app(tmp_path)

    node = discover_root_app(tmp_path)

    assert node.kind is BundleNodeKind.APP
    assert node.path == PurePosixPath("Payload/App.app")
    assert node.executable_path == PurePosixPath("Payload/App.app/App")
    assert node.source_bundle_id == "com.example.app"
    assert node.version == "123"
    assert node.short_version == "1.2.3"
    assert node.depth == 0
    assert (
        node.info_plist_sha256 == hashlib.sha256((bundle / "Info.plist").read_bytes()).hexdigest()
    )
    assert node.executable_sha256 == hashlib.sha256(b"Mach-O executable").hexdigest()


def test_rejects_missing_and_duplicate_root_candidates(tmp_path: Path) -> None:
    (tmp_path / "Payload").mkdir()
    with pytest.raises(DomainError) as missing:
        discover_root_app(tmp_path)
    assert missing.value.code is ErrorCode.INVENTORY_ROOT_AMBIGUOUS
    assert missing.value.safe_details == (("candidates", ()),)

    make_app(tmp_path, "First.app")
    make_app(tmp_path, "Second.app")
    with pytest.raises(DomainError) as duplicate:
        discover_root_app(tmp_path)
    assert duplicate.value.code is ErrorCode.INVENTORY_ROOT_AMBIGUOUS
    assert duplicate.value.safe_details == (
        ("candidates", ("Payload/First.app", "Payload/Second.app")),
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"CFBundlePackageType": "FMWK"}, "CFBundlePackageType"),
        ({"CFBundleIdentifier": "bad identifier"}, "CFBundleIdentifier"),
        ({"CFBundleExecutable": "../escape"}, "CFBundleExecutable"),
        ({"CFBundleVersion": 123}, "CFBundleVersion"),
        ({"CFBundleShortVersionString": ""}, "CFBundleShortVersionString"),
    ],
)
def test_rejects_invalid_root_metadata(
    tmp_path: Path, overrides: dict[str, object], field: str
) -> None:
    make_app(tmp_path, overrides=overrides)

    with pytest.raises(DomainError) as caught:
        discover_root_app(tmp_path)

    assert caught.value.code is ErrorCode.INVENTORY_METADATA_INVALID
    assert ("field", field) in caught.value.safe_details


@pytest.mark.parametrize(
    "field",
    [
        "CFBundlePackageType",
        "CFBundleIdentifier",
        "CFBundleExecutable",
        "CFBundleVersion",
    ],
)
def test_rejects_missing_required_plist_fields(tmp_path: Path, field: str) -> None:
    bundle = make_app(tmp_path)
    document = plistlib.loads((bundle / "Info.plist").read_bytes())
    del document[field]
    (bundle / "Info.plist").write_bytes(plistlib.dumps(document))

    with pytest.raises(DomainError) as caught:
        discover_root_app(tmp_path)

    assert ("field", field) in caught.value.safe_details


def test_rejects_missing_malformed_and_non_dictionary_plist(tmp_path: Path) -> None:
    bundle = make_app(tmp_path)
    (bundle / "Info.plist").unlink()
    with pytest.raises(DomainError, match="missing"):
        discover_root_app(tmp_path)

    (bundle / "Info.plist").write_bytes(b"invalid")
    with pytest.raises(DomainError, match="could not be decoded"):
        discover_root_app(tmp_path)

    (bundle / "Info.plist").write_bytes(plistlib.dumps(["not", "dictionary"]))
    with pytest.raises(DomainError, match="must contain a dictionary"):
        discover_root_app(tmp_path)


def test_rejects_missing_executable(tmp_path: Path) -> None:
    bundle = make_app(tmp_path)
    (bundle / "App").unlink()

    with pytest.raises(DomainError, match="executable is missing"):
        discover_root_app(tmp_path)
