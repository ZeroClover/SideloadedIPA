"""Pure construction and canonical serialization of immutable signing plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from sideloadedipa.apple_intents import derive_bundle_resource_intents
from sideloadedipa.domain import (
    BundleGraph,
    BundleNodeKind,
    CertificateIdentity,
    ExpectedNodeEntitlements,
    FrozenJsonValue,
    PolicyReconciliation,
    ProfileResourceManifest,
    ProvisioningProfile,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    Task,
    normalize_entitlements,
    thaw_json,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.profile_validation import validate_expected_entitlements
from sideloadedipa.signing_order import signing_order

_SUPPORTED_NODE_KINDS = frozenset(BundleNodeKind)
_REQUIRED_BACKEND_FEATURES = frozenset(
    {
        SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
        SigningBackendFeature.RECURSIVE_SIGNING,
    }
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
            "features": sorted(value.value for value in plan.backend.features),
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


def _fail(
    request: SigningPlanRequest,
    message: str,
    *,
    bundle_id: str | None = None,
    safe_details: tuple[tuple[str, FrozenJsonValue], ...] = (),
) -> DomainError:
    return DomainError(
        ErrorCode.SIGNING_PLAN_INVALID,
        message,
        task_name=request.task.task_name,
        bundle_id=bundle_id,
        remediation="correct the signing-plan inputs before changing the IPA",
        safe_details=safe_details,
    )


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if values.count(value) > 1}))


def _require_exact_paths(
    request: SigningPlanRequest,
    *,
    label: str,
    actual: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    duplicates = _duplicates(actual)
    missing = tuple(sorted(set(expected) - set(actual)))
    unused = tuple(sorted(set(actual) - set(expected)))
    if duplicates or missing or unused:
        raise _fail(
            request,
            f"{label} does not map exactly once to every profile-bearing node",
            safe_details=(
                ("duplicate_paths", duplicates),
                ("missing_paths", missing),
                ("unused_paths", unused),
            ),
        )


def _validate_request(request: SigningPlanRequest) -> None:
    missing_features = tuple(
        sorted(value.value for value in _REQUIRED_BACKEND_FEATURES - set(request.backend.features))
    )
    if missing_features:
        raise DomainError(
            ErrorCode.SIGNING_BACKEND_UNSUPPORTED,
            "signing backend does not provide the required plan features",
            task_name=request.task.task_name,
            remediation="use the qualified per-profile-entitlement backend from ADR 0001",
            safe_details=(
                ("backend", request.backend.name),
                ("missing_features", missing_features),
            ),
        )

    node_paths = tuple(node.path.as_posix() for node in request.graph.nodes)
    duplicate_nodes = _duplicates(node_paths)
    unsupported = tuple(
        sorted(
            node.path.as_posix()
            for node in request.graph.nodes
            if node.kind not in _SUPPORTED_NODE_KINDS
        )
    )
    if duplicate_nodes or unsupported:
        raise _fail(
            request,
            "inventory contains duplicate or unsupported signable nodes",
            safe_details=(("duplicate_paths", duplicate_nodes), ("unsupported_paths", unsupported)),
        )
    if not request.policy.valid:
        raise _fail(
            request,
            "bundle policy reconciliation contains blocking diagnostics",
            safe_details=(
                ("diagnostic_codes", tuple(value.code for value in request.policy.diagnostics)),
            ),
        )

    profile_nodes = tuple(node for node in request.graph.nodes if node.profile_bearing)
    profile_paths = tuple(sorted(node.path.as_posix() for node in profile_nodes))
    _require_exact_paths(
        request,
        label="bundle policy",
        actual=tuple(value.node_path.as_posix() for value in request.policy.matches),
        expected=profile_paths,
    )
    _require_exact_paths(
        request,
        label="expected entitlements",
        actual=tuple(value.source_path.as_posix() for value in request.expected_entitlements),
        expected=profile_paths,
    )

    for value in request.expected_entitlements:
        normalized = normalize_entitlements({key: thaw_json(child) for key, child in value.values})
        if normalized.values != value.values or normalized.sha256 != value.sha256:
            raise _fail(
                request,
                "expected entitlement evidence does not match its canonical digest",
                safe_details=(("path", value.source_path.as_posix()),),
            )

    if request.profile_manifest.task_name != request.task.task_name:
        raise _fail(request, "profile manifest belongs to a different task")

    intents = derive_bundle_resource_intents(request.task)
    target_ids = tuple(value.target_bundle_id.casefold() for value in intents)
    duplicate_targets = _duplicates(target_ids)
    if duplicate_targets:
        raise _fail(
            request,
            "multiple profile-bearing bundles resolve to the same target identifier",
            safe_details=(("target_bundle_ids", duplicate_targets),),
        )

    entries = request.profile_manifest.entries
    entry_targets = tuple(value.target_bundle_id.casefold() for value in entries)
    entry_profiles = tuple(value.profile_resource_id for value in entries)
    if (
        _duplicates(entry_targets)
        or _duplicates(entry_profiles)
        or set(entry_targets) != set(target_ids)
    ):
        raise _fail(
            request,
            "profile manifest does not map each target to one unique profile",
            safe_details=(
                ("duplicate_targets", _duplicates(entry_targets)),
                ("duplicate_profile_ids", _duplicates(entry_profiles)),
                ("missing_targets", tuple(sorted(set(target_ids) - set(entry_targets)))),
                ("unused_targets", tuple(sorted(set(entry_targets) - set(target_ids)))),
            ),
        )

    profiles_by_target: dict[str, list[ProvisioningProfile]] = {}
    for profile in request.profiles:
        profiles_by_target.setdefault(profile.bundle_id.casefold(), []).append(profile)
    conflicting = tuple(
        sorted(target for target in target_ids if len(profiles_by_target.get(target, ())) != 1)
    )
    unused_profiles = tuple(
        sorted(
            profile.resource_id
            for profile in request.profiles
            if profile.bundle_id.casefold() not in set(target_ids)
        )
    )
    if conflicting or unused_profiles:
        raise _fail(
            request,
            "profiles do not map exactly once to every target bundle",
            safe_details=(
                ("conflicting_bundle_ids", conflicting),
                ("unused_profile_ids", unused_profiles),
            ),
        )

    entries_by_target = {value.target_bundle_id.casefold(): value for value in entries}
    for target in target_ids:
        profile = profiles_by_target[target][0]
        entry = entries_by_target[target]
        if (
            entry.profile_resource_id != profile.resource_id
            or entry.profile_path != profile.path
            or entry.profile_sha256 != profile.profile_sha256
            or entry.expires_at != profile.expires_at
        ):
            raise _fail(
                request,
                "profile manifest evidence differs from the decoded profile",
                bundle_id=profile.bundle_id,
                safe_details=(("profile_resource_id", profile.resource_id),),
            )
        if entry.certificate_resource_id != request.certificate.resource_id:
            raise _fail(
                request,
                "profile manifest references a different certificate resource",
                bundle_id=profile.bundle_id,
                safe_details=(("profile_resource_id", profile.resource_id),),
            )
        if (
            profile.team_id != request.certificate.team_id
            or profile.certificate_sha256 != request.certificate.certificate_sha256
        ):
            raise _fail(
                request,
                "profile team or certificate differs from the intended signing identity",
                bundle_id=profile.bundle_id,
                safe_details=(
                    ("profile_resource_id", profile.resource_id),
                    ("profile_team_id", profile.team_id),
                ),
            )

    expected_by_path = {value.source_path: value for value in request.expected_entitlements}
    intent_by_source = {value.source_bundle_id.casefold(): value for value in intents}
    for match in request.policy.matches:
        intent = intent_by_source[match.source_bundle_id.casefold()]
        profile = profiles_by_target[intent.target_bundle_id.casefold()][0]
        expected = expected_by_path[match.node_path]
        validate_expected_entitlements(
            {key: thaw_json(value) for key, value in profile.entitlements},
            {key: thaw_json(value) for key, value in expected.values},
            bundle_id=profile.bundle_id,
        )


def build_signing_plan(request: SigningPlanRequest) -> SigningPlan:
    """Join validated planning inputs without filesystem or external-service access."""

    _validate_request(request)
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
    for order, node in enumerate(signing_order(request.graph)):
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
