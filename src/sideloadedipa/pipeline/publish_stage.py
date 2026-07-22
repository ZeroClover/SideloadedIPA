"""Verified publication candidate assembly and optional icon upload."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from sideloadedipa.adapters.publication.icons import IconError, build_icon_png
from sideloadedipa.adapters.publication.r2_store import R2Store
from sideloadedipa.domain import (
    PublicationCandidate,
    SigningPlan,
    SourceAsset,
    Task,
    VerificationResult,
)
from sideloadedipa.ipa import read_ipa_metadata
from sideloadedipa.pipeline.environment import safe_filename
from sideloadedipa.util.atomics import file_sha256


def _upload_icon(
    *,
    task: Task,
    source: SourceAsset,
    source_evidence: Mapping[str, object],
    artifact: Path,
    store: R2Store,
) -> str | None:
    if task.icon_path is None:
        return None
    try:
        png = build_icon_png(
            task.icon_path,
            task.source.location,
            ref=source.version if source_evidence.get("release_tag") is not None else None,
            ipa_path=artifact,
        )
        return store.upload_icon(task.slug, png)
    except (BotoCoreError, ClientError, IconError, OSError):
        return None


def build_publication_candidate(
    *,
    task: Task,
    source: SourceAsset,
    source_evidence: Mapping[str, object],
    artifact: Path,
    plan: SigningPlan,
    verification: VerificationResult,
    store: R2Store,
) -> PublicationCandidate:
    metadata = read_ipa_metadata(artifact)
    return PublicationCandidate(
        task.task_name,
        task.slug,
        task.app_name,
        metadata.bundle_id,
        metadata.version,
        f"{safe_filename(task.app_name)}.ipa",
        str(artifact),
        file_sha256(artifact),
        _upload_icon(
            task=task,
            source=source,
            source_evidence=source_evidence,
            artifact=artifact,
            store=store,
        ),
        task.publication_enabled,
        plan,
        verification,
    )
