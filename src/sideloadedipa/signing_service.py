"""Per-task package signing route used during the legacy-engine migration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.domain import (
    BundleGraph,
    CertificateMaterial,
    ExpectedNodeEntitlements,
    ProfileResourceManifest,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningEngine,
    SigningPlan,
    Task,
    reconcile_bundle_rules,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.ports import SigningBackend, Verifier
from sideloadedipa.signing_executor import SigningExecutionResult, execute_signing_plan
from sideloadedipa.signing_planner import SigningPlanRequest, build_signing_plan


@dataclass(frozen=True, slots=True)
class PackageSigningRequest:
    task: Task
    graph: BundleGraph
    profile_manifest: ProfileResourceManifest
    profiles: tuple[ProvisioningProfile, ...]
    certificate: CertificateMaterial
    expected_entitlements: tuple[ExpectedNodeEntitlements, ...]
    backend_identity: SigningBackendIdentity
    backend: SigningBackend
    verifier: Verifier
    source_ipa: Path
    destination_ipa: Path


@dataclass(frozen=True, slots=True)
class PlannedSigningExecution:
    plan: SigningPlan
    execution: SigningExecutionResult


def execute_package_signing(request: PackageSigningRequest) -> PlannedSigningExecution:
    """Build and execute one plan only for an explicitly opted-in task."""

    if request.task.signing_engine is not SigningEngine.PACKAGE:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "task has not enabled the package signing engine",
            task_name=request.task.task_name,
            remediation="set signing_engine = 'package' only after parity review",
        )

    plan = build_signing_plan(
        SigningPlanRequest(
            task=request.task,
            graph=request.graph,
            policy=reconcile_bundle_rules(request.task, request.graph),
            profile_manifest=request.profile_manifest,
            profiles=request.profiles,
            certificate=request.certificate.identity,
            expected_entitlements=request.expected_entitlements,
            backend=request.backend_identity,
        )
    )
    execution = execute_signing_plan(
        plan=plan,
        source_ipa=request.source_ipa,
        destination_ipa=request.destination_ipa,
        certificate=request.certificate,
        backend=request.backend,
        verifier=request.verifier,
    )
    return PlannedSigningExecution(plan, execution)
