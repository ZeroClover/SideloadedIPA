"""Recursive discovery of profile-bearing and signable Mach-O nodes."""

from __future__ import annotations

import hashlib
import json
import plistlib
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Protocol

import lief

from sideloadedipa.domain import BundleGraph, BundleNode, BundleNodeKind
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa.discovery import _discover_profile_bundle, discover_root_app

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


def _graph_digest(nodes: list[BundleNode], source_sha256: str) -> str:
    document = {
        "source_sha256": source_sha256,
        "nodes": [
            {
                "path": str(node.path),
                "kind": node.kind.value,
                "parent": str(node.parent_path) if node.parent_path else None,
                "depth": node.depth,
                "executable_sha256": node.executable_sha256,
                "bundle_id": node.source_bundle_id,
            }
            for node in sorted(nodes, key=lambda value: str(value.path))
        ],
    }
    serialized = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


def discover_bundle_graph(
    extracted_root: Path,
    source_sha256: str,
    *,
    macho_probe: MachOProbe | None = None,
) -> BundleGraph:
    """Recursively inventory supported code and preserve explicit parent edges."""

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
        is_macho = probe.is_macho(path)
        if is_macho:
            nodes.append(_file_node(extracted_root, path, BundleNodeKind.EXECUTABLE, nodes))
            continue
        if path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise _error("executable file is not a supported Mach-O binary", relative)

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

    stable_nodes = sorted(nodes, key=lambda node: str(node.path))
    return BundleGraph(
        root_path=root.path,
        nodes=tuple(stable_nodes),
        source_sha256=source_sha256,
        graph_sha256=_graph_digest(stable_nodes, source_sha256),
    )
