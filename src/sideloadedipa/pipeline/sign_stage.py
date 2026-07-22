"""Signing-cache fingerprinting and retained report validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from sideloadedipa.cache.decisions import TaskCacheRecord
from sideloadedipa.cache.fingerprint import (
    SigningCacheFingerprint,
    ToolFingerprint,
    build_signing_cache_fingerprint,
)
from sideloadedipa.domain import BundleGraph, SigningPlan, SourceAsset, Task
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.signing.service import PackageSigningRequest, plan_package_signing
from sideloadedipa.util.atomics import atomic_write_bytes, canonical_json, file_sha256


def json_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value, default=str)).hexdigest()


def template_digests(task: Task, repository_root: Path) -> tuple[tuple[str, str], ...]:
    if task.signing is None:
        return ()
    values: list[tuple[str, str]] = []
    for rule in task.signing.bundles:
        relative = rule.entitlement_policy.template_path
        if relative is None:
            continue
        values.append((relative.as_posix(), file_sha256(repository_root.joinpath(*relative.parts))))
    return tuple(sorted(values))


def build_fingerprint(
    *,
    task: Task,
    source_asset: SourceAsset,
    graph: BundleGraph,
    request: PackageSigningRequest,
    repository_root: Path,
) -> SigningCacheFingerprint:
    plan = plan_package_signing(request)
    return build_signing_cache_fingerprint(
        source=source_asset,
        policy_sha256=policy_sha256(task),
        graph=graph,
        entitlement_template_sha256=template_digests(task, repository_root),
        resource_manifest=request.profile_manifest,
        profiles=request.profiles,
        plan=plan,
        device_set_sha256=device_set_sha256(request),
        tools=(
            ToolFingerprint(
                plan.backend.name,
                plan.backend.version,
                plan.backend.executable_sha256,
            ),
        ),
    )


def policy_sha256(task: Task) -> str:
    return json_digest(asdict(task))


def device_set_sha256(request: PackageSigningRequest) -> str:
    return json_digest(
        sorted(entry.device_set_sha256 for entry in request.profile_manifest.entries)
    )


def restore_cached_signing_report(
    *,
    plan: SigningPlan,
    record: TaskCacheRecord,
    cached_path: Path,
    retained_path: Path,
) -> str:
    try:
        payload = cached_path.read_bytes()
        report_sha256 = hashlib.sha256(payload).hexdigest()
        document = json.loads(payload)
        nodes = document["nodes"]
        if (
            report_sha256 != record.signing_report_sha256
            or document["task_name"] != plan.task_name
            or document["plan_sha256"] != plan.plan_sha256
            or document["output_sha256"] != record.artifact_sha256
            or not isinstance(nodes, list)
            or {value.get("source_path") for value in nodes if isinstance(value, dict)}
            != {value.source_path.as_posix() for value in plan.nodes}
            or any(
                not isinstance(value, dict) or not isinstance(value.get("backend_evidence"), dict)
                for value in nodes
            )
        ):
            raise TypeError
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise DomainError(
            ErrorCode.CACHE_REUSE_INVALID,
            "cached signing report is missing or inconsistent",
            task_name=plan.task_name,
            remediation="discard the cache hit and rebuild the task",
        ) from error
    atomic_write_bytes(retained_path, payload)
    return report_sha256
