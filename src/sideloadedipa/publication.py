"""Verified, ordered publication of signed IPA artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sideloadedipa.domain import (
    BatchPublicationPolicy,
    PublicationCandidate,
    PublicationResult,
    StoredArtifact,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ports import VerifiedPublicationGateway
from sideloadedipa.verification import (
    verification_publication_gate,
    verification_report_sha256,
)

_COPY_BUFFER_BYTES = 1024 * 1024
_REGISTRY_FIELDS = ("slug", "name", "bundleId", "version", "ipaUrl", "iconUrl")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_COPY_BUFFER_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _publication_error(candidate: PublicationCandidate, message: str) -> DomainError:
    return DomainError(
        ErrorCode.PUBLICATION_FAILED,
        message,
        task_name=candidate.task_name,
        remediation="retain the previous publication and inspect the verification report",
    )


def _validate_candidate(candidate: PublicationCandidate) -> None:
    if not candidate.publication_enabled:
        raise _publication_error(candidate, "task publication is disabled by configuration")
    artifact = Path(candidate.artifact_path)
    if (
        not candidate.verification.passed
        or candidate.task_name != candidate.plan.task_name
        or candidate.verification.plan_sha256 != candidate.plan.plan_sha256
        or candidate.verification.artifact_sha256 != candidate.artifact_sha256
        or _file_sha256(artifact) != candidate.artifact_sha256
        or not verification_publication_gate(candidate.plan, candidate.verification)
        or candidate.verification.report_sha256
        != verification_report_sha256(candidate.plan, candidate.verification)
    ):
        raise _publication_error(candidate, "artifact did not pass the verified publication gate")


def _merge_registry(
    current: Mapping[str, object] | None,
    candidates: Sequence[PublicationCandidate],
    artifacts: Sequence[StoredArtifact],
    *,
    now: datetime,
) -> dict[str, object]:
    raw_apps = (current or {}).get("apps", [])
    if not isinstance(raw_apps, list) or any(not isinstance(app, dict) for app in raw_apps):
        raise DomainError(ErrorCode.PUBLICATION_FAILED, "registry apps value is not an object list")
    updates: dict[str, dict[str, object]] = {
        candidate.slug: {
            "slug": candidate.slug,
            "name": candidate.app_name,
            "bundleId": candidate.bundle_id,
            "version": candidate.version,
            "ipaUrl": artifact.url,
            "iconUrl": candidate.icon_url or "",
        }
        for candidate, artifact in zip(candidates, artifacts, strict=True)
    }
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_app in raw_apps:
        app = dict(raw_app)
        slug = app.get("slug")
        if isinstance(slug, str):
            seen.add(slug)
            update = updates.get(slug)
            if update is not None:
                for field in _REGISTRY_FIELDS[1:]:
                    if update[field]:
                        app[field] = update[field]
        merged.append(app)
    for candidate in candidates:
        if candidate.slug not in seen:
            merged.append(updates[candidate.slug])
            seen.add(candidate.slug)
    return {
        "updatedAt": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "apps": merged,
    }


def _referenced_keys(
    gateway: VerifiedPublicationGateway,
    document: Mapping[str, object],
) -> frozenset[str]:
    result: set[str] = set()
    apps = document.get("apps", [])
    if not isinstance(apps, list):
        return frozenset()
    for app in apps:
        if not isinstance(app, dict):
            continue
        for field in ("ipaUrl", "iconUrl"):
            url = app.get(field)
            if isinstance(url, str) and (key := gateway.object_key_from_url(url)) is not None:
                result.add(key)
    return frozenset(result)


def _unreferenced_upload_keys(
    gateway: VerifiedPublicationGateway,
    previous_registry: Mapping[str, object] | None,
    artifacts: Sequence[StoredArtifact],
    additional_keys: Sequence[str] = (),
) -> tuple[str, ...]:
    previous_keys = _referenced_keys(gateway, previous_registry or {})
    return tuple(
        key
        for key in dict.fromkeys((*(artifact.key for artifact in artifacts), *additional_keys))
        if key not in previous_keys
    )


@dataclass(frozen=True, slots=True)
class VerifiedPublicationService:
    gateway: VerifiedPublicationGateway
    batch_policy: BatchPublicationPolicy = BatchPublicationPolicy.ATOMIC

    def publish(
        self,
        candidates: Sequence[PublicationCandidate],
        *,
        now: datetime,
        failed_task_names: Sequence[str] = (),
    ) -> tuple[PublicationResult, ...]:
        if failed_task_names and self.batch_policy is BatchPublicationPolicy.ATOMIC:
            raise DomainError(
                ErrorCode.PUBLICATION_FAILED,
                "batch-atomic publication was blocked by an upstream task failure",
                remediation="resolve every selected task failure before publishing the batch",
                safe_details=(("failed_tasks", ",".join(sorted(failed_task_names))),),
            )
        if not candidates:
            return ()
        if len({candidate.task_name for candidate in candidates}) != len(candidates) or len(
            {candidate.slug for candidate in candidates}
        ) != len(candidates):
            raise DomainError(
                ErrorCode.PUBLICATION_FAILED,
                "publication task names and slugs must be unique",
            )
        for candidate in candidates:
            _validate_candidate(candidate)

        current = self.gateway.read_registry()
        previous_keys = _referenced_keys(self.gateway, current or {})
        new_icon_keys = tuple(
            key
            for candidate in candidates
            if candidate.icon_url is not None
            if (key := self.gateway.object_key_from_url(candidate.icon_url)) is not None
            if key not in previous_keys
        )
        artifacts: list[StoredArtifact] = []
        for candidate in candidates:
            try:
                artifact = self.gateway.upload_artifact(candidate)
            except Exception as error:
                unreferenced_keys = _unreferenced_upload_keys(
                    self.gateway, current, artifacts, new_icon_keys
                )
                try:
                    self.gateway.delete_uploaded(unreferenced_keys)
                except Exception as cleanup_error:
                    raise DomainError(
                        ErrorCode.PUBLICATION_FAILED,
                        "immutable artifact upload failed and compensating cleanup was incomplete",
                        task_name=candidate.task_name,
                        remediation="delete the reported unreferenced upload keys before retrying",
                        safe_details=(("unreferenced_keys", unreferenced_keys),),
                    ) from cleanup_error
                raise _publication_error(candidate, "immutable artifact upload failed") from error
            if artifact.sha256 != candidate.artifact_sha256:
                artifacts.append(artifact)
                unreferenced_keys = _unreferenced_upload_keys(
                    self.gateway, current, artifacts, new_icon_keys
                )
                try:
                    self.gateway.delete_uploaded(unreferenced_keys)
                except Exception as cleanup_error:
                    raise DomainError(
                        ErrorCode.PUBLICATION_FAILED,
                        "uploaded artifact digest differed and compensating cleanup was incomplete",
                        task_name=candidate.task_name,
                        remediation="delete the reported unreferenced upload keys before retrying",
                        safe_details=(("unreferenced_keys", unreferenced_keys),),
                    ) from cleanup_error
                raise _publication_error(candidate, "uploaded artifact digest was not confirmed")
            artifacts.append(artifact)

        document = _merge_registry(current, candidates, artifacts, now=now)
        try:
            registry_key, registry_sha256 = self.gateway.publish_registry(document)
            self.gateway.revalidate()
        except Exception as error:
            try:
                self.gateway.restore_registry(current)
            except Exception as rollback_error:
                raise DomainError(
                    ErrorCode.PUBLICATION_FAILED,
                    "registry publication and rollback both failed",
                    remediation="restore the previous registry snapshot before another publication",
                ) from rollback_error
            unreferenced_keys = _unreferenced_upload_keys(
                self.gateway, current, artifacts, new_icon_keys
            )
            try:
                self.gateway.delete_uploaded(unreferenced_keys)
            except Exception as cleanup_error:
                raise DomainError(
                    ErrorCode.PUBLICATION_FAILED,
                    "registry publication failed and compensating upload cleanup was incomplete",
                    remediation="delete the reported unreferenced upload keys before retrying",
                    safe_details=(("unreferenced_keys", unreferenced_keys),),
                ) from cleanup_error
            if isinstance(error, DomainError):
                raise
            raise DomainError(
                ErrorCode.PUBLICATION_FAILED,
                "registry publication failed and the previous snapshot was restored",
                remediation="inspect the registry adapter error before retrying",
            ) from error
        stale = self.gateway.cleanup_stale(
            [candidate.slug for candidate in candidates],
            _referenced_keys(self.gateway, document),
        )
        return tuple(
            PublicationResult(
                candidate.task_name,
                artifact.key,
                artifact.url,
                artifact.sha256,
                registry_key,
                registry_sha256,
                stale,
            )
            for candidate, artifact in zip(candidates, artifacts, strict=True)
        )
