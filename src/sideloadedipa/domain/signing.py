"""Immutable signing plans and backend results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from sideloadedipa.domain.bundle import BundleNodeKind
from sideloadedipa.domain.common import Diagnostic, FrozenJsonValue


class SigningBackendFeature(StrEnum):
    PER_PROFILE_ENTITLEMENTS = "per-profile-entitlements"
    RECURSIVE_SIGNING = "recursive-signing"


@dataclass(frozen=True, slots=True)
class SigningBackendIdentity:
    name: str
    version: str
    executable_sha256: str
    contract_version: str
    features: tuple[SigningBackendFeature, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpectedNodeEntitlements:
    source_path: PurePosixPath
    values: tuple[tuple[str, FrozenJsonValue], ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class SigningNodePlan:
    source_path: PurePosixPath
    kind: BundleNodeKind
    order: int
    target_bundle_id: str | None
    profile_resource_id: str | None
    profile_path: PurePosixPath | None
    expected_entitlements: tuple[tuple[str, FrozenJsonValue], ...]
    expected_entitlements_sha256: str


@dataclass(frozen=True, slots=True)
class SigningPlan:
    task_name: str
    source_ipa_sha256: str
    graph_sha256: str
    certificate_sha256: str
    backend: SigningBackendIdentity
    nodes: tuple[SigningNodePlan, ...]
    plan_sha256: str


@dataclass(frozen=True, slots=True)
class SigningNodeResult:
    source_path: PurePosixPath
    signed_executable_sha256: str
    embedded_profile_sha256: str | None
    signed_entitlements_sha256: str
    duration_seconds: float
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class SigningResult:
    plan_sha256: str
    output_path: PurePosixPath
    output_sha256: str
    backend: SigningBackendIdentity
    nodes: tuple[SigningNodeResult, ...]
    duration_seconds: float
    diagnostics: tuple[Diagnostic, ...] = ()
