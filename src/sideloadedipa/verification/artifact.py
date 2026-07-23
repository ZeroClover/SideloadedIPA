"""Independent extraction of signed executable entitlement evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from sideloadedipa.domain import FrozenJsonValue, SigningPlan, normalize_entitlements
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import LiefEntitlementInspector
from sideloadedipa.ipa.entitlements import MachOEntitlementEvidence
from sideloadedipa.util.atomics import file_sha256


class SignedEntitlementInspector(Protocol):
    def inspect(self, path: Path) -> MachOEntitlementEvidence: ...


@dataclass(frozen=True, slots=True)
class EntitlementRepresentationEvidence:
    values: tuple[tuple[str, FrozenJsonValue], ...]
    semantic_sha256: str
    raw_sha256: str


@dataclass(frozen=True, slots=True)
class SignedEntitlementSliceEvidence:
    architecture: str
    xml: EntitlementRepresentationEvidence | None
    der: EntitlementRepresentationEvidence | None


@dataclass(frozen=True, slots=True)
class SignedNodeEntitlementEvidence:
    source_path: PurePosixPath
    executable_path: PurePosixPath
    executable_sha256: str
    slices: tuple[SignedEntitlementSliceEvidence, ...]


@dataclass(frozen=True, slots=True)
class SignedArtifactEntitlementEvidence:
    plan_sha256: str
    artifact_sha256: str
    nodes: tuple[SignedNodeEntitlementEvidence, ...]


def _representation(
    values: Mapping[str, object] | None,
    raw: bytes | None,
) -> EntitlementRepresentationEvidence | None:
    if values is None or raw is None:
        return None
    normalized = normalize_entitlements(values)
    return EntitlementRepresentationEvidence(
        normalized.values,
        normalized.sha256,
        hashlib.sha256(raw).hexdigest(),
    )


def _evidence_error(plan: SigningPlan, node_path: PurePosixPath, message: str) -> DomainError:
    return DomainError(
        ErrorCode.VERIFICATION_EVIDENCE_INVALID,
        message,
        task_name=plan.task_name,
        remediation="discard the artifact and inspect the planned executable signing evidence",
        safe_details=(("node_path", node_path.as_posix()),),
    )


def _executable(root: Path, plan: SigningPlan, path: PurePosixPath) -> Path:
    if path.is_absolute() or ".." in path.parts:
        raise _evidence_error(plan, path, "planned executable path is not workspace-relative")
    resolved_root = root.resolve()
    executable = root.joinpath(*path.parts).resolve()
    if not executable.is_relative_to(resolved_root) or not executable.is_file():
        raise _evidence_error(plan, path, "planned executable is missing from the signed IPA")
    return executable


def inspect_signed_entitlements(
    plan: SigningPlan,
    signed_root: Path,
    artifact_sha256: str,
    *,
    inspector: SignedEntitlementInspector | None = None,
) -> SignedArtifactEntitlementEvidence:
    """Inspect every planned executable in the pass's shared extracted tree."""

    selected_inspector = inspector or LiefEntitlementInspector()
    nodes: list[SignedNodeEntitlementEvidence] = []
    for node in plan.nodes:
        executable = _executable(signed_root, plan, node.executable_path)
        try:
            inspected = selected_inspector.inspect(executable)
        except DomainError as error:
            raise _evidence_error(
                plan,
                node.source_path,
                "signed executable entitlement evidence could not be read",
            ) from error
        slices = tuple(
            SignedEntitlementSliceEvidence(
                value.architecture,
                _representation(value.xml, value.xml_raw),
                _representation(value.der, value.der_raw),
            )
            for value in inspected.slices
        )
        if not slices:
            raise _evidence_error(
                plan,
                node.source_path,
                "signed executable has no architecture entitlement evidence",
            )
        nodes.append(
            SignedNodeEntitlementEvidence(
                node.source_path,
                node.executable_path,
                file_sha256(executable),
                slices,
            )
        )
    return SignedArtifactEntitlementEvidence(
        plan.plan_sha256,
        artifact_sha256,
        tuple(nodes),
    )
