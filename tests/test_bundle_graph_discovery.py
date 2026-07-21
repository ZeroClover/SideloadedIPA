"""Tests for recursive IPA code graph discovery."""

from __future__ import annotations

import plistlib
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import BundleNodeKind
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import LiefMachOProbe, discover_bundle_graph


class MarkerMachOProbe:
    def is_macho(self, path: Path) -> bool:
        return path.read_bytes().startswith(b"MACHO")


def make_bundle(
    root: Path,
    relative: str,
    *,
    identifier: str,
    executable: str,
    package_type: str,
) -> Path:
    bundle = root / relative
    bundle.mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundlePackageType": package_type,
                "CFBundleIdentifier": identifier,
                "CFBundleExecutable": executable,
                "CFBundleVersion": "1",
            }
        )
    )
    binary = bundle / executable
    binary.write_bytes(f"MACHO:{relative}".encode())
    binary.chmod(0o755)
    return bundle


def make_graph_tree(root: Path) -> None:
    app = make_bundle(
        root,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    extension = make_bundle(
        root,
        "Payload/App.app/PlugIns/Share.appex",
        identifier="com.example.app.Share",
        executable="Share",
        package_type="XPC!",
    )
    make_bundle(
        root,
        "Payload/App.app/Watch/Nested.app",
        identifier="com.example.app.Nested",
        executable="Nested",
        package_type="APPL",
    )
    framework = extension / "Frameworks" / "Kit.framework"
    framework.mkdir(parents=True)
    (framework / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "Kit",
                "CFBundleIdentifier": "com.example.Kit",
                "CFBundleVersion": "2",
            }
        )
    )
    (framework / "Kit").write_bytes(b"MACHO:framework")
    (framework / "Kit").chmod(0o755)
    (framework / "Support.dylib").write_bytes(b"MACHO:dylib")
    (app / "Helpers").mkdir()
    (app / "Helpers" / "Runner").write_bytes(b"MACHO:helper")
    (app / "Helpers" / "Runner").chmod(0o755)


def test_discovers_nested_bundle_and_signable_graph(tmp_path: Path) -> None:
    make_graph_tree(tmp_path)

    graph = discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())
    nodes = {str(node.path): node for node in graph.nodes}

    assert nodes["Payload/App.app"].kind is BundleNodeKind.APP
    assert nodes["Payload/App.app/PlugIns/Share.appex"].parent_path == PurePosixPath(
        "Payload/App.app"
    )
    framework = nodes["Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.framework"]
    assert framework.kind is BundleNodeKind.FRAMEWORK
    assert framework.parent_path == PurePosixPath("Payload/App.app/PlugIns/Share.appex")
    assert framework.depth == 2
    dylib = nodes["Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.framework/Support.dylib"]
    assert dylib.kind is BundleNodeKind.DYLIB
    assert dylib.parent_path == framework.path
    assert nodes["Payload/App.app/Helpers/Runner"].kind is BundleNodeKind.EXECUTABLE
    assert nodes["Payload/App.app/Watch/Nested.app"].profile_bearing is True
    assert len(graph.graph_sha256) == 64


def test_graph_is_stable_for_same_tree(tmp_path: Path) -> None:
    make_graph_tree(tmp_path)

    first = discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())
    second = discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())

    assert first == second


def test_rejects_non_macho_bundle_dylib_and_unknown_executable(tmp_path: Path) -> None:
    app = make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    (app / "App").write_bytes(b"not macho")
    with pytest.raises(DomainError) as root_error:
        discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())
    assert root_error.value.code is ErrorCode.INVENTORY_EXECUTABLE_INVALID

    (app / "App").write_bytes(b"MACHO:root")
    (app / "Invalid.dylib").write_bytes(b"not macho")
    with pytest.raises(DomainError, match="dylib"):
        discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())

    (app / "Invalid.dylib").unlink()
    unknown = app / "unknown-tool"
    unknown.write_bytes(b"script")
    unknown.chmod(0o755)
    with pytest.raises(DomainError, match="not a supported Mach-O"):
        discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())


def test_rejects_duplicate_profile_bundle_identifiers(tmp_path: Path) -> None:
    make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    make_bundle(
        tmp_path,
        "Payload/App.app/PlugIns/Duplicate.appex",
        identifier="COM.EXAMPLE.APP",
        executable="Duplicate",
        package_type="XPC!",
    )

    with pytest.raises(DomainError) as caught:
        discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())

    assert caught.value.code is ErrorCode.INVENTORY_DUPLICATE_BUNDLE_ID


def test_rejects_unknown_executable_bundle_type(tmp_path: Path) -> None:
    app = make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    make_bundle(
        app,
        "PlugIns/Unknown.xpc",
        identifier="com.example.unknown",
        executable="Unknown",
        package_type="XPC!",
    )

    with pytest.raises(DomainError, match="unsupported executable bundle type .xpc"):
        discover_bundle_graph(tmp_path, "a" * 64, macho_probe=MarkerMachOProbe())


def test_lief_probe_rejects_non_macho_and_parses_minimal_thin_header(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid"
    invalid.write_bytes(b"not Mach-O")
    assert LiefMachOProbe().is_macho(invalid) is False

    # mach_header_64: arm64 MH_EXECUTE with no load commands.
    thin = tmp_path / "thin"
    thin.write_bytes(
        bytes.fromhex(
            "cffaedfe" "0c000001" "00000000" "02000000" "00000000" "00000000" "00000000" "00000000"
        )
    )
    assert LiefMachOProbe().is_macho(thin) is True

    truncated = tmp_path / "truncated"
    truncated.write_bytes(bytes.fromhex("cffaedfe") + b"broken")
    assert LiefMachOProbe().is_macho(truncated) is False
