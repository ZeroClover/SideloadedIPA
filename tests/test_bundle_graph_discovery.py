"""Tests for recursive IPA code graph discovery."""

from __future__ import annotations

import hashlib
import json
import plistlib
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import BundleNodeKind
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import (
    EntitlementSliceEvidence,
    LiefMachOProbe,
    MachOEntitlementEvidence,
    canonical_graph_json,
    discover_bundle_graph,
)


class MarkerMachOProbe:
    def is_macho(self, path: Path) -> bool:
        return path.read_bytes().startswith(b"MACHO")


class MarkerEntitlementInspector:
    def inspect(self, path: Path) -> MachOEntitlementEvidence:
        document = {"application-identifier": f"TEAM.{path.name}"}
        xml = plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True)
        der = f"DER:{path.name}".encode()
        return MachOEntitlementEvidence(
            (
                EntitlementSliceEvidence(
                    index=0,
                    architecture="ARM64",
                    xml_raw=xml,
                    der_raw=der,
                    xml=document,
                    der=document,
                ),
            )
        )


class FixedEntitlementInspector:
    def __init__(self, evidence: MachOEntitlementEvidence) -> None:
        self.evidence = evidence

    def inspect(self, path: Path) -> MachOEntitlementEvidence:
        return self.evidence


class FailingEntitlementInspector:
    def inspect(self, path: Path) -> MachOEntitlementEvidence:
        raise DomainError(
            ErrorCode.INVENTORY_ENTITLEMENTS_INVALID,
            "fixture entitlement failure",
            safe_details=(("path", str(path)), ("slice_index", 0)),
        )


class UnsignedEntitlementInspector:
    def inspect(self, path: Path) -> MachOEntitlementEvidence:
        raise DomainError(
            ErrorCode.INVENTORY_ENTITLEMENTS_INVALID,
            "Mach-O slice has no embedded code signature",
            safe_details=(
                ("path", str(path)),
                ("slice_index", 0),
                ("reason", "missing-code-signature"),
            ),
        )


def discover(root: Path, source_sha256: str = "a" * 64):
    return discover_bundle_graph(
        root,
        source_sha256,
        macho_probe=MarkerMachOProbe(),
        entitlement_inspector=MarkerEntitlementInspector(),
    )


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
    (app / "embedded.mobileprovision").write_bytes(b"root profile")
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

    graph = discover(tmp_path)
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
    root = nodes["Payload/App.app"]
    assert root.embedded_profile_sha256 == hashlib.sha256(b"root profile").hexdigest()
    assert root.entitlements == (("application-identifier", "TEAM.App"),)
    assert root.entitlement_slices[0].architecture == "ARM64"
    expected_xml = plistlib.dumps(
        {"application-identifier": "TEAM.App"}, fmt=plistlib.FMT_XML, sort_keys=True
    )
    assert root.xml_entitlements_sha256 == hashlib.sha256(expected_xml).hexdigest()
    assert root.der_entitlements_sha256 == hashlib.sha256(b"DER:App").hexdigest()
    assert nodes["Payload/App.app/PlugIns/Share.appex"].embedded_profile_sha256 is None
    assert len(graph.graph_sha256) == 64


def test_graph_is_stable_for_same_tree(tmp_path: Path) -> None:
    make_graph_tree(tmp_path)

    first = discover(tmp_path)
    second = discover(tmp_path)

    assert first == second
    manifest = json.loads(canonical_graph_json(first))
    assert manifest["schema_version"] == 1
    assert manifest["graph_sha256"] == first.graph_sha256
    assert [item["path"] for item in manifest["nodes"]] == sorted(
        item["path"] for item in manifest["nodes"]
    )


def test_graph_digest_changes_with_profile_evidence(tmp_path: Path) -> None:
    make_graph_tree(tmp_path)
    first = discover(tmp_path)

    (tmp_path / "Payload/App.app/embedded.mobileprovision").write_bytes(b"changed")
    second = discover(tmp_path)

    assert first.graph_sha256 != second.graph_sha256


def test_rejects_xml_der_entitlement_disagreement(tmp_path: Path) -> None:
    make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    evidence = MachOEntitlementEvidence(
        (
            EntitlementSliceEvidence(
                index=0,
                architecture="ARM64",
                xml_raw=b"xml",
                der_raw=b"der",
                xml={"value": []},
                der={"value": {}},
            ),
        )
    )

    with pytest.raises(DomainError) as caught:
        discover_bundle_graph(
            tmp_path,
            "a" * 64,
            macho_probe=MarkerMachOProbe(),
            entitlement_inspector=FixedEntitlementInspector(evidence),
        )

    assert caught.value.code is ErrorCode.INVENTORY_ENTITLEMENTS_DISAGREE
    assert dict(caught.value.safe_details)["architecture"] == "ARM64"


def test_rejects_slice_without_decoded_entitlement_evidence(tmp_path: Path) -> None:
    make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    evidence = MachOEntitlementEvidence(
        (EntitlementSliceEvidence(0, "ARM64", None, None, None, None),)
    )

    with pytest.raises(DomainError, match="no decoded evidence"):
        discover_bundle_graph(
            tmp_path,
            "a" * 64,
            macho_probe=MarkerMachOProbe(),
            entitlement_inspector=FixedEntitlementInspector(evidence),
        )


def test_entitlement_failure_replaces_workspace_path_with_bundle_path(tmp_path: Path) -> None:
    make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )

    with pytest.raises(DomainError) as caught:
        discover_bundle_graph(
            tmp_path,
            "a" * 64,
            macho_probe=MarkerMachOProbe(),
            entitlement_inspector=FailingEntitlementInspector(),
        )

    assert caught.value.bundle_id == "com.example.app"
    assert dict(caught.value.safe_details)["path"] == "Payload/App.app/App"


def test_allows_explicit_unsigned_source_without_inventing_entitlements(tmp_path: Path) -> None:
    bundle = make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    (bundle / "embedded.mobileprovision").write_bytes(b"source profile")

    graph = discover_bundle_graph(
        tmp_path,
        "a" * 64,
        macho_probe=MarkerMachOProbe(),
        entitlement_inspector=UnsignedEntitlementInspector(),
        allow_missing_code_signature=True,
    )

    root = graph.nodes[0]
    assert root.entitlements == ()
    assert root.entitlement_slices == ()
    assert root.embedded_profile_sha256 == hashlib.sha256(b"source profile").hexdigest()


def test_rejects_entitlement_disagreement_between_fat_slices(tmp_path: Path) -> None:
    make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    evidence = MachOEntitlementEvidence(
        (
            EntitlementSliceEvidence(0, "ARM64", b"one", None, {"value": 1}, None),
            EntitlementSliceEvidence(1, "X86_64", b"two", None, {"value": 2}, None),
        )
    )

    with pytest.raises(DomainError, match="different entitlements") as caught:
        discover_bundle_graph(
            tmp_path,
            "a" * 64,
            macho_probe=MarkerMachOProbe(),
            entitlement_inspector=FixedEntitlementInspector(evidence),
        )

    assert caught.value.code is ErrorCode.INVENTORY_ENTITLEMENTS_DISAGREE


def test_rejects_non_macho_bundle_and_dylib_but_ignores_executable_resource(
    tmp_path: Path,
) -> None:
    app = make_bundle(
        tmp_path,
        "Payload/App.app",
        identifier="com.example.app",
        executable="App",
        package_type="APPL",
    )
    (app / "App").write_bytes(b"not macho")
    with pytest.raises(DomainError) as root_error:
        discover(tmp_path)
    assert root_error.value.code is ErrorCode.INVENTORY_EXECUTABLE_INVALID

    (app / "App").write_bytes(b"MACHO:root")
    (app / "Invalid.dylib").write_bytes(b"not macho")
    with pytest.raises(DomainError, match="dylib"):
        discover(tmp_path)

    (app / "Invalid.dylib").unlink()
    unknown = app / "unknown-tool"
    unknown.write_bytes(b"script")
    unknown.chmod(0o755)
    graph = discover(tmp_path)
    assert all(node.path.name != "unknown-tool" for node in graph.nodes)


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
        discover(tmp_path)

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
        discover(tmp_path)


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
