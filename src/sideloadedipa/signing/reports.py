"""Canonical, redacted signing result and evidence reports."""

from __future__ import annotations

import hashlib

from sideloadedipa.domain import (
    SigningNodeResult,
    SigningPlan,
    SigningResult,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.util.atomics import canonical_json, diagnostic_document

_ARGV_OPTIONS = frozenset({"-c", "-e", "-f", "-k", "-m", "-o"})


def _node_result_document(node: SigningNodeResult) -> dict[str, object]:
    return {
        "source_path": node.source_path.as_posix(),
        "signed_executable_sha256": node.signed_executable_sha256,
        "embedded_profile_sha256": node.embedded_profile_sha256,
        "signed_entitlements_sha256": node.signed_entitlements_sha256,
        "duration_seconds": node.duration_seconds,
        "diagnostics": [diagnostic_document(value) for value in node.diagnostics],
    }


def _argv_shape(argv: tuple[str, ...]) -> list[str]:
    return [value if value in _ARGV_OPTIONS else "<redacted>" for value in argv]


def _result_document(result: SigningResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "plan_sha256": result.plan_sha256,
        "output_name": result.output_path.name,
        "output_sha256": result.output_sha256,
        "backend": {
            "name": result.backend.name,
            "version": result.backend.version,
            "executable_sha256": result.backend.executable_sha256,
            "contract_version": result.backend.contract_version,
            "features": sorted(value.value for value in result.backend.features),
        },
        "nodes": [_node_result_document(value) for value in result.nodes],
        "duration_seconds": result.duration_seconds,
        "diagnostics": [diagnostic_document(value) for value in result.diagnostics],
        "backend_argv_shape": _argv_shape(result.backend_argv),
    }


def signing_result_sha256(result: SigningResult) -> str:
    """Digest the complete canonical result without serializing private argv values."""

    return hashlib.sha256(canonical_json(_result_document(result))).hexdigest()


def _invalid_report(plan: SigningPlan, message: str) -> DomainError:
    return DomainError(
        ErrorCode.SIGNING_VERIFICATION_FAILED,
        message,
        task_name=plan.task_name,
        remediation="discard the inconsistent result and rerun the validated signing plan",
    )


def canonical_signing_report_json(plan: SigningPlan, result: SigningResult) -> bytes:
    """Join plan and backend result evidence without embedding sensitive inputs."""

    if result.plan_sha256 != plan.plan_sha256:
        raise _invalid_report(plan, "signing result references a different plan")
    if result.backend != plan.backend:
        raise _invalid_report(plan, "signing result references a different backend")

    result_nodes = {value.source_path: value for value in result.nodes}
    if len(result_nodes) != len(result.nodes):
        raise _invalid_report(plan, "signing result contains duplicate node evidence")
    planned_paths = {value.source_path for value in plan.nodes}
    if unknown := sorted(path.as_posix() for path in result_nodes if path not in planned_paths):
        raise _invalid_report(plan, f"signing result contains unplanned node evidence: {unknown}")

    nodes: list[dict[str, object]] = []
    for node in plan.nodes:
        backend_evidence = result_nodes.get(node.source_path)
        nodes.append(
            {
                "source_path": node.source_path.as_posix(),
                "kind": node.kind.value,
                "order": node.order,
                "target_bundle_id": node.target_bundle_id,
                "profile_resource_id": node.profile_resource_id,
                "profile_sha256": node.profile_sha256,
                "expected_entitlements_sha256": node.expected_entitlements_sha256,
                "backend_evidence": (
                    _node_result_document(backend_evidence)
                    if backend_evidence is not None
                    else None
                ),
            }
        )

    return canonical_json(
        {
            "schema_version": 1,
            "task_name": plan.task_name,
            "plan_sha256": plan.plan_sha256,
            "result_sha256": signing_result_sha256(result),
            "output_sha256": result.output_sha256,
            "backend": {
                "name": result.backend.name,
                "version": result.backend.version,
                "executable_sha256": result.backend.executable_sha256,
                "contract_version": result.backend.contract_version,
            },
            "duration_seconds": result.duration_seconds,
            "backend_argv_shape": _argv_shape(result.backend_argv),
            "nodes": nodes,
        }
    )
