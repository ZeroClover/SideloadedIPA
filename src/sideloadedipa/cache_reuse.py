"""Security-sensitive revalidation for cache hits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sideloadedipa.cache_decisions import TaskCacheRecord
from sideloadedipa.domain import (
    Diagnostic,
    DiagnosticSeverity,
    ProvisioningProfile,
    SigningPlan,
    VerificationResult,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ports import Verifier
from sideloadedipa.verification import (
    verification_publication_gate,
    verification_report_sha256,
)

_COPY_BUFFER_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CachePrerequisiteState:
    ready: bool
    snapshot_sha256: str
    diagnostics: tuple[Diagnostic, ...] = ()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_COPY_BUFFER_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _reject(
    plan: SigningPlan,
    message: str,
    *,
    details: tuple[tuple[str, str], ...] = (),
) -> DomainError:
    return DomainError(
        ErrorCode.CACHE_REUSE_INVALID,
        message,
        task_name=plan.task_name,
        remediation="discard the cache hit and rebuild the task from current inputs",
        safe_details=details,
    )


def _validate_current_profiles(
    plan: SigningPlan,
    profiles: tuple[ProvisioningProfile, ...],
    *,
    now: datetime,
    refresh_threshold: timedelta,
) -> None:
    by_id = {profile.resource_id: profile for profile in profiles}
    planned_ids = {
        node.profile_resource_id for node in plan.nodes if node.profile_resource_id is not None
    }
    if len(by_id) != len(profiles) or set(by_id) != planned_ids:
        raise _reject(plan, "current profiles do not map exactly to the cached signing plan")
    for node in plan.nodes:
        if node.profile_resource_id is None:
            continue
        profile = by_id[node.profile_resource_id]
        if (
            profile.profile_sha256 != node.profile_sha256
            or profile.certificate_sha256 != plan.certificate_sha256
            or profile.bundle_id != node.target_bundle_id
        ):
            raise _reject(
                plan,
                "current profile identity differs from the cached signing plan",
                details=(("profile_resource_id", profile.resource_id),),
            )
        if profile.created_at > now or profile.expires_at - now <= refresh_threshold:
            raise _reject(
                plan,
                "current profile is not valid beyond the refresh threshold",
                details=(("profile_resource_id", profile.resource_id),),
            )


def revalidate_cached_artifact(
    *,
    plan: SigningPlan,
    cache_record: TaskCacheRecord,
    artifact: Path,
    prerequisites: CachePrerequisiteState,
    profiles: tuple[ProvisioningProfile, ...],
    now: datetime,
    refresh_threshold: timedelta,
    verifier: Verifier,
) -> VerificationResult:
    """Recheck current prerequisites and reopen a cached IPA through the full verifier."""

    if cache_record.task_name != plan.task_name:
        raise _reject(plan, "cache record belongs to another task")
    if not prerequisites.ready or any(
        diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in prerequisites.diagnostics
    ):
        raise _reject(
            plan,
            "current signing prerequisites are not ready",
            details=(("snapshot_sha256", prerequisites.snapshot_sha256),),
        )
    _validate_current_profiles(plan, profiles, now=now, refresh_threshold=refresh_threshold)

    artifact_sha256 = _file_sha256(artifact)
    if artifact_sha256 != cache_record.artifact_sha256:
        raise _reject(plan, "cached artifact digest differs from its cache record")

    result = verifier.verify(plan, artifact)
    if (
        result.artifact_sha256 != artifact_sha256
        or result.plan_sha256 != plan.plan_sha256
        or not verification_publication_gate(plan, result)
        or result.passed is not True
        or result.report_sha256 != verification_report_sha256(plan, result)
        or result.report_sha256 != cache_record.verification_report_sha256
    ):
        raise _reject(plan, "cached artifact did not pass current full verification")
    return result
