"""Signed IPA graph parity and protected-payload verification."""

from __future__ import annotations

import hashlib
import json
import plistlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    BundleNode,
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    SigningPlan,
    VerificationFinding,
)
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.ipa.graph import MachOProbe, discover_bundle_structure
from sideloadedipa.workspace import task_workspace

_COPY_BUFFER_BYTES = 1024 * 1024
_BUNDLE_KINDS = {BundleNodeKind.APP, BundleNodeKind.APP_EXTENSION, BundleNodeKind.FRAMEWORK}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_COPY_BUFFER_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _structure_document(nodes: tuple[BundleNode, ...]) -> list[dict[str, object]]:
    return [
        {
            "path": node.path.as_posix(),
            "kind": node.kind.value,
            "parent": node.parent_path.as_posix() if node.parent_path is not None else None,
            "executable": node.executable_path.as_posix(),
        }
        for node in sorted(nodes, key=lambda value: value.path.as_posix())
    ]


def _plan_structure(plan: SigningPlan) -> list[dict[str, object]]:
    return [
        {
            "path": node.source_path.as_posix(),
            "kind": node.kind.value,
            "executable": node.executable_path.as_posix(),
        }
        for node in sorted(plan.nodes, key=lambda value: value.source_path.as_posix())
    ]


def _diagnostic(plan: SigningPlan, check: str, message: str) -> Diagnostic:
    return Diagnostic(
        f"verification.{check.replace('-', '_')}",
        DiagnosticSeverity.ERROR,
        message,
        task_name=plan.task_name,
        remediation="discard the output and rebuild it from the inspected source and signing plan",
    )


def _finding(
    plan: SigningPlan,
    root: PurePosixPath,
    check: str,
    expected: str,
    actual: str,
    *,
    message: str,
) -> VerificationFinding:
    passed = expected == actual
    return VerificationFinding(
        root,
        check,
        passed,
        expected,
        actual,
        () if passed else (_diagnostic(plan, check, message),),
    )


def _identifiers(plan: SigningPlan, nodes: tuple[BundleNode, ...]) -> dict[str, str]:
    planned = {
        node.source_path: node.target_bundle_id
        for node in plan.nodes
        if node.target_bundle_id is not None
    }
    actual = {node.path: node.source_bundle_id for node in nodes if node.profile_bearing}
    return {
        path.as_posix(): actual.get(path) or "<missing>"
        for path in sorted(planned, key=lambda value: value.as_posix())
    }


def _expected_identifiers(plan: SigningPlan) -> dict[str, str]:
    return {
        node.source_path.as_posix(): node.target_bundle_id
        for node in sorted(plan.nodes, key=lambda value: value.source_path.as_posix())
        if node.target_bundle_id is not None
    }


def _mutable_path(path: PurePosixPath, nodes: tuple[BundleNode, ...]) -> bool:
    for node in nodes:
        if path == node.executable_path:
            return True
        if node.kind not in _BUNDLE_KINDS or not path.is_relative_to(node.path):
            continue
        relative = path.relative_to(node.path)
        if relative == PurePosixPath("Info.plist"):
            return True
        if node.profile_bearing and relative == PurePosixPath("embedded.mobileprovision"):
            return True
        if relative.parts and relative.parts[0] == "_CodeSignature":
            return True
    return False


def _protected_files(root: Path, nodes: tuple[BundleNode, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if not _mutable_path(relative, nodes):
            result[relative.as_posix()] = _file_sha256(path)
    return result


def _protected_info(root: Path, nodes: tuple[BundleNode, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in nodes:
        if node.kind not in _BUNDLE_KINDS:
            continue
        path = root.joinpath(*node.path.parts, "Info.plist")
        document = plistlib.loads(path.read_bytes())
        if not isinstance(document, Mapping):
            raise ValueError("bundle Info.plist is not a dictionary")
        protected = {str(key): value for key, value in document.items()}
        protected.pop("CFBundleIdentifier", None)
        result[node.path.as_posix()] = hashlib.sha256(
            plistlib.dumps(protected, fmt=plistlib.FMT_BINARY, sort_keys=True)
        ).hexdigest()
    return result


def verify_output_integrity(
    plan: SigningPlan,
    source_ipa: Path,
    signed_ipa: Path,
    *,
    macho_probe: MachOProbe | None = None,
) -> tuple[VerificationFinding, ...]:
    """Compare signed output structure and protected content with its exact source and plan."""

    workspace_base = signed_ipa.parent / ".sideloadedipa-integrity-verification"
    remove_workspace_base = not workspace_base.exists()
    try:
        with task_workspace(workspace_base, plan.task_name) as workspace:
            source_root = workspace.root / "source"
            output_root = workspace.root / "output"
            extract_ipa_safely(source_ipa, source_root)
            extract_ipa_safely(signed_ipa, output_root)
            source_nodes = discover_bundle_structure(source_root, macho_probe=macho_probe)
            output_nodes = discover_bundle_structure(output_root, macho_probe=macho_probe)
            root = source_nodes[0].path

            source_sha256 = _file_sha256(source_ipa)
            output_sha256 = _file_sha256(signed_ipa)
            findings = [
                _finding(
                    plan,
                    root,
                    "source-artifact",
                    plan.source_ipa_sha256,
                    source_sha256,
                    message="verification source does not match the signing plan",
                ),
                VerificationFinding(
                    root,
                    "safe-output-archive",
                    True,
                    output_sha256,
                    output_sha256,
                ),
            ]

            source_structure = _structure_document(source_nodes)
            output_structure = _structure_document(output_nodes)
            plan_structure = _plan_structure(plan)
            source_plan_structure = [
                {key: value for key, value in item.items() if key != "parent"}
                for item in source_structure
            ]
            findings.extend(
                (
                    _finding(
                        plan,
                        root,
                        "source-plan-node-set",
                        _canonical_sha256(plan_structure),
                        _canonical_sha256(source_plan_structure),
                        message="source executable inventory does not match the signing plan",
                    ),
                    _finding(
                        plan,
                        root,
                        "output-graph-parity",
                        _canonical_sha256(source_structure),
                        _canonical_sha256(output_structure),
                        message="signed output graph differs from the inspected source graph",
                    ),
                    _finding(
                        plan,
                        root,
                        "planned-identifiers",
                        _canonical_sha256(_expected_identifiers(plan)),
                        _canonical_sha256(_identifiers(plan, output_nodes)),
                        message="signed bundle identifiers do not match the signing plan",
                    ),
                    _finding(
                        plan,
                        root,
                        "executable-set",
                        _canonical_sha256(
                            sorted(node.executable_path.as_posix() for node in plan.nodes)
                        ),
                        _canonical_sha256(
                            sorted(node.executable_path.as_posix() for node in output_nodes)
                        ),
                        message="signed output contains a missing or unplanned executable",
                    ),
                    _finding(
                        plan,
                        root,
                        "protected-info-plists",
                        _canonical_sha256(_protected_info(source_root, source_nodes)),
                        _canonical_sha256(_protected_info(output_root, source_nodes)),
                        message="a non-identifier Info.plist value changed during signing",
                    ),
                    _finding(
                        plan,
                        root,
                        "protected-payload",
                        _canonical_sha256(_protected_files(source_root, source_nodes)),
                        _canonical_sha256(_protected_files(output_root, source_nodes)),
                        message="non-signing payload content changed during signing",
                    ),
                )
            )
            return tuple(findings)
    finally:
        if remove_workspace_base:
            try:
                workspace_base.rmdir()
            except OSError:
                pass
