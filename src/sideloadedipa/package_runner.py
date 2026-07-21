"""Filesystem composition for one production package-engine signing task."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sideloadedipa.adapters.signing import ZsignBackend
from sideloadedipa.certificate_identity import load_p12_certificate_material
from sideloadedipa.domain import BundleGraph, EntitlementMode, ProfileType, Task
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import discover_bundle_graph, extract_ipa_safely
from sideloadedipa.profile_storage import load_profile_manifest
from sideloadedipa.signing_inputs import load_synced_profiles
from sideloadedipa.signing_service import (
    PlannedSigningExecution,
    build_package_signing_request,
    execute_package_signing,
)
from sideloadedipa.verification.service import PackageVerifier


def _source_entitlements_required(task: Task) -> bool:
    return task.signing is not None and any(
        rule.entitlement_policy.mode is EntitlementMode.PRESERVE_SOURCE
        for rule in task.signing.bundles
    )


def inspect_source_graph(source_ipa: Path, *, task: Task | None = None) -> BundleGraph:
    with tempfile.TemporaryDirectory(prefix="sideloadedipa-production-inventory-") as directory:
        extracted = Path(directory) / "extracted"
        extract_ipa_safely(source_ipa, extracted)
        source_sha256 = hashlib.sha256(source_ipa.read_bytes()).hexdigest()
        return discover_bundle_graph(
            extracted,
            source_sha256,
            allow_missing_code_signature=(
                task is not None and not _source_entitlements_required(task)
            ),
        )


def run_package_signing(
    *,
    task: Task,
    source_ipa: Path,
    destination_ipa: Path,
    profile_root: Path,
    p12_path: Path,
    p12_password: str,
    private_directory: Path,
    zsign_executable: Path,
    zsign_sha256: str,
    repository_root: Path,
    now: datetime | None = None,
) -> PlannedSigningExecution:
    """Load current synced inputs, sign one task, and verify before promotion."""

    current_time = now or datetime.now(timezone.utc)
    manifest = load_profile_manifest(profile_root, task.task_name)
    certificate_ids = tuple(sorted({entry.certificate_resource_id for entry in manifest.entries}))
    if len(certificate_ids) != 1:
        raise DomainError(
            ErrorCode.SIGNING_PLAN_INVALID,
            "profile manifest must reference exactly one signing certificate",
            task_name=task.task_name,
            remediation="rerun package profile sync with the configured development certificate",
            safe_details=(("certificate_resource_ids", certificate_ids),),
        )
    certificate = load_p12_certificate_material(
        p12_path,
        p12_password,
        resource_id=certificate_ids[0],
        output_directory=private_directory,
    )
    profile_type = (
        task.signing.profile_type if task.signing is not None else ProfileType.IOS_APP_DEVELOPMENT
    )
    profiles = load_synced_profiles(
        profile_root=profile_root,
        manifest=manifest,
        profile_type=profile_type,
        certificate=certificate.identity,
        now=current_time,
    )
    graph = inspect_source_graph(source_ipa, task=task)
    backend = ZsignBackend(
        executable=zsign_executable,
        expected_executable_sha256=zsign_sha256,
        profile_root=profile_root,
    )
    verifier = PackageVerifier(source_ipa, profiles, current_time)
    request = build_package_signing_request(
        task=task,
        graph=graph,
        profile_manifest=manifest,
        profiles=profiles,
        certificate=certificate,
        backend_identity=backend.identity(),
        backend=backend,
        verifier=verifier,
        source_ipa=source_ipa,
        destination_ipa=destination_ipa,
        repository_root=repository_root,
    )
    return execute_package_signing(request)
