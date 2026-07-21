"""Per-task package signing route used during the legacy-engine migration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.apple_intents import derive_bundle_resource_intents
from sideloadedipa.config import EntitlementTemplateContext, load_entitlement_template
from sideloadedipa.domain import (
    BundleGraph,
    CertificateMaterial,
    EntitlementContext,
    EntitlementMode,
    ExpectedNodeEntitlements,
    ProfileResourceManifest,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningPlan,
    Task,
    materialize_entitlements,
    normalize_entitlements,
    reconcile_bundle_rules,
    thaw_json,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
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


def build_package_signing_request(
    *,
    task: Task,
    graph: BundleGraph,
    profile_manifest: ProfileResourceManifest,
    profiles: tuple[ProvisioningProfile, ...],
    certificate: CertificateMaterial,
    backend_identity: SigningBackendIdentity,
    backend: SigningBackend,
    verifier: Verifier,
    source_ipa: Path,
    destination_ipa: Path,
    repository_root: Path,
) -> PackageSigningRequest:
    """Join validated production inputs and materialize each bundle policy."""

    policy = reconcile_bundle_rules(task, graph)
    if not policy.valid:
        raise DomainError(
            ErrorCode.SIGNING_PLAN_INVALID,
            "bundle policy reconciliation contains blocking diagnostics",
            task_name=task.task_name,
            remediation="correct the inventory-to-policy mapping before signing",
            safe_details=(("diagnostic_codes", tuple(value.code for value in policy.diagnostics)),),
        )
    intent_values = derive_bundle_resource_intents(task)
    intents = {value.source_bundle_id.casefold(): value for value in intent_values}
    profiles_by_target = {value.bundle_id.casefold(): value for value in profiles}
    nodes_by_path = {value.path: value for value in graph.nodes}
    expected: list[ExpectedNodeEntitlements] = []
    for match in policy.matches:
        intent = (
            intent_values[0]
            if task.signing is None and len(intent_values) == 1
            else intents.get(match.source_bundle_id.casefold())
        )
        if intent is None:
            raise DomainError(
                ErrorCode.SIGNING_PLAN_INVALID,
                "bundle resource intent is missing for a reconciled policy",
                task_name=task.task_name,
                bundle_id=match.source_bundle_id,
            )
        profile = profiles_by_target.get(intent.target_bundle_id.casefold())
        if profile is None:
            raise DomainError(
                ErrorCode.SIGNING_PLAN_INVALID,
                "decoded profile is missing for a reconciled bundle",
                task_name=task.task_name,
                bundle_id=intent.target_bundle_id,
            )
        node = nodes_by_path[match.node_path]
        source_document = {key: thaw_json(value) for key, value in node.entitlements}
        profile_document = {key: thaw_json(value) for key, value in profile.entitlements}
        entitlement_policy = match.rule.entitlement_policy
        if entitlement_policy.mode is EntitlementMode.PROFILE:
            materialized = normalize_entitlements(profile_document)
        else:
            prefix = profile.application_identifier[: -len(intent.target_bundle_id)]
            template = None
            if entitlement_policy.mode is EntitlementMode.TEMPLATE:
                if entitlement_policy.template_path is None:
                    raise ConfigurationError(
                        ErrorCode.ENTITLEMENTS_TEMPLATE_MISSING,
                        "template entitlement policy has no template path",
                        task_name=task.task_name,
                        bundle_id=intent.target_bundle_id,
                    )
                template = load_entitlement_template(
                    repository_root,
                    entitlement_policy.template_path,
                    EntitlementTemplateContext(
                        profile.team_id,
                        prefix,
                        intent.target_bundle_id,
                        task.signing.app_groups if task.signing is not None else (),
                    ),
                )
            materialized = materialize_entitlements(
                entitlement_policy,
                source_document,
                EntitlementContext(
                    profile.team_id,
                    prefix,
                    match.source_bundle_id,
                    intent.target_bundle_id,
                ),
                profile_entitlements=profile_document,
                template_entitlements=template,
            )
        expected.append(
            ExpectedNodeEntitlements(
                match.node_path,
                materialized.values,
                materialized.sha256,
            )
        )
    return PackageSigningRequest(
        task,
        graph,
        profile_manifest,
        profiles,
        certificate,
        tuple(expected),
        backend_identity,
        backend,
        verifier,
        source_ipa,
        destination_ipa,
    )


def execute_package_signing(request: PackageSigningRequest) -> PlannedSigningExecution:
    """Build, execute, and independently verify one package signing plan."""

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
