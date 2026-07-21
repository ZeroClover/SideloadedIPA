"""Pure construction and canonical serialization of immutable signing plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from sideloadedipa.apple_intents import derive_bundle_resource_intents
from sideloadedipa.domain import (
    BundleGraph,
    CertificateIdentity,
    ExpectedNodeEntitlements,
    PolicyReconciliation,
    ProfileResourceManifest,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    Task,
    normalize_entitlements,
    thaw_json,
)


@dataclass(frozen=True, slots=True)
class SigningPlanRequest:
    task: Task
    graph: BundleGraph
    policy: PolicyReconciliation
    profile_manifest: ProfileResourceManifest
    profiles: tuple[ProvisioningProfile, ...]
    certificate: CertificateIdentity
    expected_entitlements: tuple[ExpectedNodeEntitlements, ...]
    backend: SigningBackendIdentity


def _node_document(node: SigningNodePlan) -> dict[str, object]:
    return {
        "source_path": node.source_path.as_posix(),
        "kind": node.kind.value,
        "order": node.order,
        "target_bundle_id": node.target_bundle_id,
        "profile_resource_id": node.profile_resource_id,
        "profile_path": node.profile_path.as_posix() if node.profile_path is not None else None,
        "expected_entitlements": {
            key: thaw_json(value) for key, value in node.expected_entitlements
        },
        "expected_entitlements_sha256": node.expected_entitlements_sha256,
    }


def _plan_document(plan: SigningPlan) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_name": plan.task_name,
        "source_ipa_sha256": plan.source_ipa_sha256,
        "graph_sha256": plan.graph_sha256,
        "certificate_sha256": plan.certificate_sha256,
        "backend": {
            "name": plan.backend.name,
            "version": plan.backend.version,
            "executable_sha256": plan.backend.executable_sha256,
            "contract_version": plan.backend.contract_version,
        },
        "nodes": [_node_document(node) for node in plan.nodes],
    }


def canonical_signing_plan_json(plan: SigningPlan) -> bytes:
    document = _plan_document(plan)
    document["plan_sha256"] = plan.plan_sha256
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _plan_sha256(plan: SigningPlan) -> str:
    return hashlib.sha256(
        json.dumps(_plan_document(plan), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_signing_plan(request: SigningPlanRequest) -> SigningPlan:
    """Join validated planning inputs without filesystem or external-service access."""

    intents = {
        value.source_bundle_id.casefold(): value
        for value in derive_bundle_resource_intents(request.task)
    }
    matches = {value.node_path: value for value in request.policy.matches}
    manifest_entries = {
        value.target_bundle_id.casefold(): value for value in request.profile_manifest.entries
    }
    profiles = {value.resource_id: value for value in request.profiles}
    expected = {value.source_path: value for value in request.expected_entitlements}
    empty = normalize_entitlements({})

    nodes = []
    for order, node in enumerate(sorted(request.graph.nodes, key=lambda value: str(value.path))):
        if not node.profile_bearing:
            nodes.append(
                SigningNodePlan(
                    source_path=node.path,
                    kind=node.kind,
                    order=order,
                    target_bundle_id=None,
                    profile_resource_id=None,
                    profile_path=None,
                    expected_entitlements=empty.values,
                    expected_entitlements_sha256=empty.sha256,
                )
            )
            continue

        match = matches[node.path]
        intent = intents[match.source_bundle_id.casefold()]
        entry = manifest_entries[intent.target_bundle_id.casefold()]
        profile = profiles[entry.profile_resource_id]
        entitlement = expected[node.path]
        nodes.append(
            SigningNodePlan(
                source_path=node.path,
                kind=node.kind,
                order=order,
                target_bundle_id=intent.target_bundle_id,
                profile_resource_id=profile.resource_id,
                profile_path=entry.profile_path,
                expected_entitlements=entitlement.values,
                expected_entitlements_sha256=entitlement.sha256,
            )
        )

    plan = SigningPlan(
        task_name=request.task.task_name,
        source_ipa_sha256=request.graph.source_sha256,
        graph_sha256=request.graph.graph_sha256,
        certificate_sha256=request.certificate.certificate_sha256,
        backend=request.backend,
        nodes=tuple(nodes),
        plan_sha256="",
    )
    return replace(plan, plan_sha256=_plan_sha256(plan))
