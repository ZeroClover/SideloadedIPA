"""Pure classification of observed Apple resource requirements."""

from __future__ import annotations

from collections import Counter

from sideloadedipa.domain import (
    AppleOperation,
    AppleResource,
    AppleResourcePlan,
    AppleResourceRequirement,
    Diagnostic,
    DiagnosticSeverity,
    OperationDisposition,
)
from sideloadedipa.errors import DomainError, ErrorCode


def _requirement_key(requirement: AppleResourceRequirement) -> tuple[str, str, str, str]:
    return (
        requirement.bundle_id or "",
        requirement.resource_kind.value,
        requirement.action,
        requirement.target,
    )


def _validate_requirement(requirement: AppleResourceRequirement) -> None:
    if not requirement.action or not requirement.target:
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "Apple resource requirement action and target must be non-empty",
            bundle_id=requirement.bundle_id,
        )
    if requirement.missing_disposition is OperationDisposition.NO_OP:
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "a missing Apple resource cannot be classified as no-op",
            bundle_id=requirement.bundle_id,
            remediation="choose safe-automatic, manual-required, or blocked",
            safe_details=(("target", requirement.target),),
        )
    if not requirement.remediation:
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "Apple resource requirement remediation must be non-empty",
            bundle_id=requirement.bundle_id,
            safe_details=(("target", requirement.target),),
        )


def _diagnostic(
    requirement: AppleResourceRequirement,
    task_name: str,
    disposition: OperationDisposition,
    *,
    duplicate: bool = False,
) -> Diagnostic:
    if duplicate:
        code = "apple.requirement_duplicate"
        message = "the Apple resource requirement is declared more than once"
        severity = DiagnosticSeverity.ERROR
    elif len(set(requirement.matching_resource_ids)) > 1:
        code = "apple.resource_ambiguous"
        message = "multiple Apple resources match one exact requirement"
        severity = DiagnosticSeverity.ERROR
    else:
        code = f"apple.resource_{disposition.value}"
        message = {
            OperationDisposition.SAFE_AUTOMATIC: "the missing Apple resource can be added safely",
            OperationDisposition.MANUAL_REQUIRED: "the missing Apple resource requires human action",
            OperationDisposition.BLOCKED: "the missing Apple resource blocks reconciliation",
        }[disposition]
        severity = {
            OperationDisposition.SAFE_AUTOMATIC: DiagnosticSeverity.INFO,
            OperationDisposition.MANUAL_REQUIRED: DiagnosticSeverity.WARNING,
            OperationDisposition.BLOCKED: DiagnosticSeverity.ERROR,
        }[disposition]
    return Diagnostic(
        code=code,
        severity=severity,
        message=message,
        task_name=task_name,
        bundle_id=requirement.bundle_id,
        remediation=requirement.remediation,
        details=(
            ("resource_kind", requirement.resource_kind.value),
            ("target", requirement.target),
            ("matching_resource_ids", tuple(sorted(set(requirement.matching_resource_ids)))),
        ),
    )


def plan_apple_resources(
    *,
    task_name: str,
    snapshot_sha256: str,
    requirements: tuple[AppleResourceRequirement, ...],
    resources: tuple[AppleResource, ...] = (),
) -> AppleResourcePlan:
    """Classify a complete read-only requirement set without performing I/O."""

    if not task_name or not snapshot_sha256:
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "Apple resource planning requires task and snapshot identities",
        )

    for requirement in requirements:
        _validate_requirement(requirement)

    counts = Counter(_requirement_key(requirement) for requirement in requirements)
    operations = []
    handled: set[tuple[str, str, str, str]] = set()
    for requirement in sorted(requirements, key=_requirement_key):
        key = _requirement_key(requirement)
        if key in handled:
            continue
        handled.add(key)

        resource_ids = tuple(sorted(set(requirement.matching_resource_ids)))
        diagnostics: tuple[Diagnostic, ...] = ()
        existing_resource_id = resource_ids[0] if len(resource_ids) == 1 else None
        if counts[key] > 1:
            disposition = OperationDisposition.BLOCKED
            existing_resource_id = None
            diagnostics = (_diagnostic(requirement, task_name, disposition, duplicate=True),)
        elif len(resource_ids) > 1:
            disposition = OperationDisposition.BLOCKED
            diagnostics = (_diagnostic(requirement, task_name, disposition),)
        elif resource_ids:
            disposition = OperationDisposition.NO_OP
        else:
            disposition = requirement.missing_disposition
            diagnostics = (_diagnostic(requirement, task_name, disposition),)

        operations.append(
            AppleOperation(
                disposition=disposition,
                resource_kind=requirement.resource_kind,
                action=requirement.action,
                target=requirement.target,
                existing_resource_id=existing_resource_id,
                bundle_id=requirement.bundle_id,
                diagnostics=diagnostics,
            )
        )

    return AppleResourcePlan(
        snapshot_sha256=snapshot_sha256,
        operations=tuple(operations),
        resources=tuple(sorted(resources, key=lambda value: (value.kind.value, value.resource_id))),
    )
