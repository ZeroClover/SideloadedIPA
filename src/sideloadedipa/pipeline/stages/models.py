"""Typed values shared by concrete production stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sideloadedipa.cache.fingerprint import SigningCacheFingerprint
from sideloadedipa.domain.bundle import BundleGraph
from sideloadedipa.domain.config import Task
from sideloadedipa.domain.pipeline import SourceAsset
from sideloadedipa.pipeline.inspection import ResolvedSource
from sideloadedipa.signing.service import PackageSigningRequest, plan_package_signing
from sideloadedipa.sources.download import DownloadedSource


@dataclass(frozen=True, slots=True)
class SourceContext:
    task: Task
    resolved: ResolvedSource
    downloaded: DownloadedSource
    source: SourceAsset
    graph: BundleGraph
    source_started_at: datetime | None = None
    source_completed_at: datetime | None = None
    inventory_started_at: datetime | None = None
    inventory_completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PreparedContext:
    source: SourceContext
    request: PackageSigningRequest
    fingerprint: SigningCacheFingerprint

    @property
    def plan(self):  # type: ignore[no-untyped-def]
        return plan_package_signing(self.request)
