"""Synthetic LiveContainer inventory variants."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from sideloadedipa.domain import BundleNodeKind
from sideloadedipa.ipa import (
    EntitlementSliceEvidence,
    MachOEntitlementEvidence,
    discover_bundle_graph,
)

FIXTURES = Path(__file__).parent / "fixtures" / "inventory"
STANDARD_IDS = {
    "com.kdt.livecontainer",
    "com.kdt.livecontainer.LaunchAppExtension",
    "com.kdt.livecontainer.LiveProcess",
    "com.kdt.livecontainer.ShareExtension",
}


class MarkerMachOProbe:
    def is_macho(self, path: Path) -> bool:
        return path.read_bytes().startswith(b"MACHO")


class FixtureEntitlementInspector:
    def inspect(self, path: Path) -> MachOEntitlementEvidence:
        document = {"fixture-executable": path.name}
        return MachOEntitlementEvidence(
            (
                EntitlementSliceEvidence(
                    0,
                    "ARM64",
                    plistlib.dumps(document),
                    b"fixture-der",
                    document,
                    document,
                ),
            )
        )


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def write_bundle(root: Path, specification: dict[str, str]) -> None:
    bundle = root / specification["path"]
    bundle.mkdir(parents=True)
    executable = specification["executable"]
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": specification["bundle_id"],
                "CFBundleExecutable": executable,
                "CFBundlePackageType": specification["package_type"],
                "CFBundleVersion": "1",
            }
        )
    )
    (bundle / executable).write_bytes(b"MACHO:" + specification["path"].encode())


def materialize_fixture(root: Path, fixture: dict[str, Any]) -> None:
    for specification in fixture["profile_bundles"]:
        write_bundle(root, specification)
    for specification in fixture["frameworks"]:
        write_bundle(root, {**specification, "package_type": "FMWK"})
    for relative in fixture["dylibs"]:
        path = root / relative
        path.write_bytes(b"MACHO:dylib")


@pytest.mark.parametrize(
    ("fixture_name", "expected_ids"),
    [
        ("livecontainer-standard.json", STANDARD_IDS),
        (
            "livecontainer-sidestore.json",
            STANDARD_IDS | {"com.kdt.livecontainer.LiveWidget"},
        ),
    ],
)
def test_livecontainer_inventory_variants(
    tmp_path: Path, fixture_name: str, expected_ids: set[str]
) -> None:
    fixture = load_fixture(fixture_name)
    materialize_fixture(tmp_path, fixture)

    graph = discover_bundle_graph(
        tmp_path,
        "a" * 64,
        macho_probe=MarkerMachOProbe(),
        entitlement_inspector=FixtureEntitlementInspector(),
    )
    profile_nodes = [node for node in graph.nodes if node.profile_bearing]

    assert {node.source_bundle_id for node in profile_nodes} == expected_ids
    assert len(profile_nodes) == len(expected_ids)
    assert graph.nodes[0].path == PurePosixPath("Payload/LiveContainer.app")

    nodes = {str(node.path): node for node in graph.nodes}
    extension_path = PurePosixPath("Payload/LiveContainer.app/PlugIns/LiveProcess.appex")
    framework_path = extension_path / "Frameworks/LiveProcessKit.framework"
    dylib_path = framework_path / "Support.dylib"
    assert nodes[str(extension_path)].depth == 1
    assert nodes[str(framework_path)].kind is BundleNodeKind.FRAMEWORK
    assert nodes[str(framework_path)].parent_path == extension_path
    assert nodes[str(framework_path)].depth == 2
    assert nodes[str(dylib_path)].kind is BundleNodeKind.DYLIB
    assert nodes[str(dylib_path)].parent_path == framework_path
    assert nodes[str(dylib_path)].depth == 3
