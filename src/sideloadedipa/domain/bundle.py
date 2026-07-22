"""Immutable IPA inventory graph values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from sideloadedipa.domain.common import FrozenJsonValue


class BundleNodeKind(StrEnum):
    APP = "app"
    APP_EXTENSION = "app-extension"
    FRAMEWORK = "framework"
    DYLIB = "dylib"
    EXECUTABLE = "executable"


@dataclass(frozen=True, slots=True)
class EntitlementSliceDigest:
    architecture: str
    xml_sha256: str | None
    der_sha256: str | None


@dataclass(frozen=True, slots=True)
class BundleNode:
    path: PurePosixPath
    kind: BundleNodeKind
    depth: int
    executable_path: PurePosixPath
    executable_sha256: str
    parent_path: PurePosixPath | None = None
    source_bundle_id: str | None = None
    info_plist_sha256: str | None = None
    version: str | None = None
    short_version: str | None = None
    embedded_profile_sha256: str | None = None
    xml_entitlements_sha256: str | None = None
    der_entitlements_sha256: str | None = None
    entitlement_slices: tuple[EntitlementSliceDigest, ...] = ()
    entitlements: tuple[tuple[str, FrozenJsonValue], ...] = ()

    @property
    def profile_bearing(self) -> bool:
        return self.kind in {BundleNodeKind.APP, BundleNodeKind.APP_EXTENSION}


@dataclass(frozen=True, slots=True)
class BundleGraph:
    root_path: PurePosixPath
    nodes: tuple[BundleNode, ...]
    source_sha256: str
    graph_sha256: str
