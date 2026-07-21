"""Recursive discovery of profile-bearing and signable Mach-O nodes."""

from __future__ import annotations

import hashlib
import json
import plistlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Protocol

import lief

from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    EntitlementSliceDigest,
    FrozenJsonObject,
    FrozenJsonValue,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa.discovery import _discover_profile_bundle, discover_root_app
from sideloadedipa.ipa.entitlements import LiefEntitlementInspector, MachOEntitlementEvidence

_MACHO_MAGICS = {
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xca\xfe\xba\xbf",
    b"\xbe\xba\xfe\xca",
    b"\xbf\xba\xfe\xca",
}


class MachOProbe(Protocol):
    def is_macho(self, path: Path) -> bool: ...


class EntitlementInspector(Protocol):
    def inspect(self, path: Path) -> MachOEntitlementEvidence: ...


class LiefMachOProbe:
    """Validate thin and fat Mach-O binaries with LIEF's quick parser."""

    def is_macho(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                if handle.read(4) not in _MACHO_MAGICS:
                    return False
            parsed = lief.MachO.parse(path, config=lief.MachO.ParserConfig.quick)
            if parsed is None or parsed.size == 0:
                return False
            return all(
                binary.header.cpu_type is not lief.MachO.Header.CPU_TYPE.ANY
                and binary.header.file_type is not lief.MachO.Header.FILE_TYPE.UNKNOWN
                and binary.header.magic in set(lief.MachO.MACHO_TYPES)
                for binary in parsed
            )
        except (OSError, RuntimeError):
            return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _error(message: str, path: PurePosixPath) -> DomainError:
    return DomainError(
        ErrorCode.INVENTORY_EXECUTABLE_INVALID,
        message,
        remediation="remove unsupported executable content or add a reviewed node type",
        safe_details=(("path", str(path)),),
    )


def _relative(root: Path, path: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(root).as_posix())


def _parent_node(path: PurePosixPath, nodes: list[BundleNode]) -> BundleNode:
    parents = [node for node in nodes if node.path != path and path.is_relative_to(node.path)]
    if not parents:
        raise _error("signable node is outside the root application", path)
    return max(parents, key=lambda node: len(node.path.parts))


def _framework_node(
    extracted_root: Path,
    relative_bundle: PurePosixPath,
    parent: BundleNode,
    probe: MachOProbe,
) -> BundleNode:
    bundle = extracted_root / Path(*relative_bundle.parts)
    info_path = bundle / "Info.plist"
    info_sha256: str | None = None
    source_bundle_id: str | None = None
    version: str | None = None
    short_version: str | None = None
    executable_name = bundle.stem
    if info_path.is_file():
        try:
            raw_info = info_path.read_bytes()
            document = plistlib.loads(raw_info)
        except (OSError, plistlib.InvalidFileException) as error:
            raise _error("framework Info.plist could not be decoded", relative_bundle) from error
        if not isinstance(document, Mapping):
            raise _error("framework Info.plist must contain a dictionary", relative_bundle)
        configured_executable = document.get("CFBundleExecutable")
        if configured_executable is not None:
            if (
                not isinstance(configured_executable, str)
                or Path(configured_executable).name != configured_executable
            ):
                raise _error("framework CFBundleExecutable must be a file name", relative_bundle)
            executable_name = configured_executable
        identifier = document.get("CFBundleIdentifier")
        source_bundle_id = identifier if isinstance(identifier, str) else None
        build = document.get("CFBundleVersion")
        version = build if isinstance(build, str) else None
        short = document.get("CFBundleShortVersionString")
        short_version = short if isinstance(short, str) else None
        info_sha256 = hashlib.sha256(raw_info).hexdigest()

    executable = bundle / executable_name
    executable_relative = relative_bundle / executable_name
    if not executable.is_file() or not probe.is_macho(executable):
        raise _error("framework executable is missing or is not Mach-O", executable_relative)
    return BundleNode(
        path=relative_bundle,
        kind=BundleNodeKind.FRAMEWORK,
        depth=parent.depth + 1,
        executable_path=executable_relative,
        executable_sha256=_sha256(executable),
        parent_path=parent.path,
        source_bundle_id=source_bundle_id,
        info_plist_sha256=info_sha256,
        version=version,
        short_version=short_version,
    )


def _file_node(
    extracted_root: Path,
    path: Path,
    kind: BundleNodeKind,
    nodes: list[BundleNode],
) -> BundleNode:
    relative = _relative(extracted_root, path)
    parent = _parent_node(relative, nodes)
    return BundleNode(
        path=relative,
        kind=kind,
        depth=parent.depth + 1,
        executable_path=relative,
        executable_sha256=_sha256(path),
        parent_path=parent.path,
    )


def _reject_unsupported_executable_bundles(
    root_directory: Path,
    extracted_root: Path,
    known_bundles: set[PurePosixPath],
    probe: MachOProbe,
) -> None:
    for info_path in sorted(root_directory.rglob("Info.plist")):
        bundle = info_path.parent
        relative_bundle = _relative(extracted_root, bundle)
        if relative_bundle in known_bundles:
            continue
        try:
            document = plistlib.loads(info_path.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            continue
        if not isinstance(document, Mapping):
            continue
        executable_name = document.get("CFBundleExecutable")
        if not isinstance(executable_name, str) or not executable_name:
            continue
        executable = bundle / executable_name
        if executable.is_file() and probe.is_macho(executable):
            raise _error(
                f"unsupported executable bundle type {bundle.suffix or '<none>'}",
                relative_bundle,
            )


def _json_value(value: FrozenJsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: _json_value(child) for key, child in value.items}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _graph_document(
    root_path: PurePosixPath,
    nodes: list[BundleNode] | tuple[BundleNode, ...],
    source_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "root_path": str(root_path),
        "nodes": [
            {
                "path": str(node.path),
                "kind": node.kind.value,
                "parent": str(node.parent_path) if node.parent_path else None,
                "depth": node.depth,
                "executable_path": str(node.executable_path),
                "executable_sha256": node.executable_sha256,
                "source_bundle_id": node.source_bundle_id,
                "info_plist_sha256": node.info_plist_sha256,
                "version": node.version,
                "short_version": node.short_version,
                "embedded_profile_sha256": node.embedded_profile_sha256,
                "xml_entitlements_sha256": node.xml_entitlements_sha256,
                "der_entitlements_sha256": node.der_entitlements_sha256,
                "entitlement_slices": [
                    {
                        "architecture": item.architecture,
                        "xml_sha256": item.xml_sha256,
                        "der_sha256": item.der_sha256,
                    }
                    for item in node.entitlement_slices
                ],
                "entitlements": {key: _json_value(value) for key, value in node.entitlements},
            }
            for node in sorted(nodes, key=lambda value: str(value.path))
        ],
    }


def _canonical_json(document: Mapping[str, object]) -> bytes:
    serialized = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return serialized


def canonical_graph_json(graph: BundleGraph) -> bytes:
    """Serialize one graph as schema-versioned canonical JSON."""

    document = _graph_document(graph.root_path, graph.nodes, graph.source_sha256)
    document["graph_sha256"] = graph.graph_sha256
    return _canonical_json(document)


def _graph_digest(root_path: PurePosixPath, nodes: list[BundleNode], source_sha256: str) -> str:
    return hashlib.sha256(
        _canonical_json(_graph_document(root_path, nodes, source_sha256))
    ).hexdigest()


def _raw_digest(values: list[tuple[str, bytes]]) -> str | None:
    if not values:
        return None
    if len(values) == 1:
        return hashlib.sha256(values[0][1]).hexdigest()
    digest = hashlib.sha256()
    for architecture, raw in values:
        encoded_architecture = architecture.encode("utf-8")
        digest.update(len(encoded_architecture).to_bytes(4, "big"))
        digest.update(encoded_architecture)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _profile_evidence(
    extracted_root: Path,
    node: BundleNode,
    inspector: EntitlementInspector,
) -> BundleNode:
    executable = extracted_root / Path(*node.executable_path.parts)
    try:
        evidence = inspector.inspect(executable)
    except DomainError as error:
        details = tuple(
            (key, str(node.executable_path) if key == "path" else value)
            for key, value in error.safe_details
        )
        raise DomainError(
            error.code,
            error.message,
            task_name=error.task_name,
            bundle_id=error.bundle_id or node.source_bundle_id,
            remediation=error.remediation,
            safe_details=details,
        ) from error
    normalized = []
    slice_digests: list[EntitlementSliceDigest] = []
    xml_raw: list[tuple[str, bytes]] = []
    der_raw: list[tuple[str, bytes]] = []
    for item in evidence.slices:
        xml = normalize_entitlements(item.xml) if item.xml is not None else None
        der = normalize_entitlements(item.der) if item.der is not None else None
        if xml is not None and der is not None and xml.sha256 != der.sha256:
            raise DomainError(
                ErrorCode.INVENTORY_ENTITLEMENTS_DISAGREE,
                "XML and DER entitlements disagree",
                bundle_id=node.source_bundle_id,
                remediation="replace or re-sign the source executable with matching evidence",
                safe_details=(
                    ("path", str(node.executable_path)),
                    ("architecture", item.architecture),
                    ("xml_sha256", xml.sha256),
                    ("der_sha256", der.sha256),
                ),
            )
        selected = xml or der
        if selected is None:
            raise DomainError(
                ErrorCode.INVENTORY_ENTITLEMENTS_INVALID,
                "entitlement inspector returned no decoded evidence",
                bundle_id=node.source_bundle_id,
                safe_details=(("path", str(node.executable_path)),),
            )
        normalized.append((item.architecture, selected))
        xml_hash = hashlib.sha256(item.xml_raw).hexdigest() if item.xml_raw is not None else None
        der_hash = hashlib.sha256(item.der_raw).hexdigest() if item.der_raw is not None else None
        slice_digests.append(EntitlementSliceDigest(item.architecture, xml_hash, der_hash))
        if item.xml_raw is not None:
            xml_raw.append((item.architecture, item.xml_raw))
        if item.der_raw is not None:
            der_raw.append((item.architecture, item.der_raw))

    if not normalized:
        raise DomainError(
            ErrorCode.INVENTORY_ENTITLEMENTS_INVALID,
            "entitlement inspector returned no Mach-O slices",
            bundle_id=node.source_bundle_id,
            safe_details=(("path", str(node.executable_path)),),
        )
    baseline = normalized[0][1]
    for architecture, current in normalized[1:]:
        if current.sha256 != baseline.sha256:
            raise DomainError(
                ErrorCode.INVENTORY_ENTITLEMENTS_DISAGREE,
                "Mach-O slices contain different entitlements",
                bundle_id=node.source_bundle_id,
                remediation="replace or re-sign the source executable consistently",
                safe_details=(
                    ("path", str(node.executable_path)),
                    ("first_architecture", normalized[0][0]),
                    ("second_architecture", architecture),
                ),
            )

    bundle = extracted_root / Path(*node.path.parts)
    profile = bundle / "embedded.mobileprovision"
    if profile.exists() and not profile.is_file():
        raise DomainError(
            ErrorCode.INVENTORY_METADATA_INVALID,
            "embedded provisioning profile is not a regular file",
            bundle_id=node.source_bundle_id,
            safe_details=(("path", str(node.path / "embedded.mobileprovision")),),
        )
    return replace(
        node,
        embedded_profile_sha256=_sha256(profile) if profile.is_file() else None,
        xml_entitlements_sha256=_raw_digest(xml_raw),
        der_entitlements_sha256=_raw_digest(der_raw),
        entitlement_slices=tuple(slice_digests),
        entitlements=baseline.values,
    )


def discover_bundle_structure(
    extracted_root: Path,
    *,
    macho_probe: MachOProbe | None = None,
) -> tuple[BundleNode, ...]:
    """Discover stable code nodes without interpreting entitlement evidence."""

    probe = macho_probe or LiefMachOProbe()
    root = discover_root_app(extracted_root)
    root_executable = extracted_root / Path(*root.executable_path.parts)
    if not probe.is_macho(root_executable):
        raise _error("root application executable is not Mach-O", root.executable_path)
    nodes = [root]

    root_directory = extracted_root / Path(*root.path.parts)
    bundle_directories = sorted(
        (
            path
            for path in root_directory.rglob("*")
            if path.is_dir() and path.suffix in {".app", ".appex", ".framework"}
        ),
        key=lambda path: (len(path.parts), path.as_posix()),
    )
    for bundle in bundle_directories:
        relative = _relative(extracted_root, bundle)
        parent = _parent_node(relative, nodes)
        if bundle.suffix == ".framework":
            nodes.append(_framework_node(extracted_root, relative, parent, probe))
            continue
        kind = BundleNodeKind.APP if bundle.suffix == ".app" else BundleNodeKind.APP_EXTENSION
        package_type = "APPL" if kind is BundleNodeKind.APP else "XPC!"
        node = _discover_profile_bundle(
            extracted_root,
            relative,
            kind=kind,
            package_type=package_type,
            depth=parent.depth + 1,
            parent_path=parent.path,
            label="nested application" if kind is BundleNodeKind.APP else "application extension",
        )
        executable = extracted_root / Path(*node.executable_path.parts)
        if not probe.is_macho(executable):
            raise _error("bundle executable is not Mach-O", node.executable_path)
        nodes.append(node)

    _reject_unsupported_executable_bundles(
        root_directory,
        extracted_root,
        {node.path for node in nodes if node.path.suffix},
        probe,
    )

    owned_executables = {node.executable_path for node in nodes}
    for path in sorted(root_directory.rglob("*")):
        if not path.is_file():
            continue
        relative = _relative(extracted_root, path)
        if relative in owned_executables:
            continue
        if path.suffix == ".dylib":
            if not probe.is_macho(path):
                raise _error("dylib is not a valid Mach-O binary", relative)
            nodes.append(_file_node(extracted_root, path, BundleNodeKind.DYLIB, nodes))
            continue
        if probe.is_macho(path):
            nodes.append(_file_node(extracted_root, path, BundleNodeKind.EXECUTABLE, nodes))

    profile_ids: dict[str, PurePosixPath] = {}
    for node in nodes:
        if not node.profile_bearing or node.source_bundle_id is None:
            continue
        key = node.source_bundle_id.casefold()
        if key in profile_ids:
            raise DomainError(
                ErrorCode.INVENTORY_DUPLICATE_BUNDLE_ID,
                "profile-bearing bundles have duplicate source identifiers",
                bundle_id=node.source_bundle_id,
                remediation="select a corrected IPA with unique bundle identifiers",
                safe_details=(
                    ("first_path", str(profile_ids[key])),
                    ("second_path", str(node.path)),
                ),
            )
        profile_ids[key] = node.path

    return tuple(sorted(nodes, key=lambda node: str(node.path)))


def discover_bundle_graph(
    extracted_root: Path,
    source_sha256: str,
    *,
    macho_probe: MachOProbe | None = None,
    entitlement_inspector: EntitlementInspector | None = None,
) -> BundleGraph:
    """Discover code and require valid entitlement evidence for profile bundles."""

    inspector = entitlement_inspector or LiefEntitlementInspector()
    structural_nodes = discover_bundle_structure(
        extracted_root,
        macho_probe=macho_probe,
    )
    nodes = [
        _profile_evidence(extracted_root, node, inspector) if node.profile_bearing else node
        for node in structural_nodes
    ]
    root_path = structural_nodes[0].path
    return BundleGraph(
        root_path=root_path,
        nodes=tuple(nodes),
        source_sha256=source_sha256,
        graph_sha256=_graph_digest(root_path, nodes, source_sha256),
    )
