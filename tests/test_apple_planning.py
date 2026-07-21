"""Tests for pure Apple resource operation classification."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sideloadedipa.apple_planning import plan_apple_resources
from sideloadedipa.domain import (
    AppleResourceKind,
    AppleResourceRequirement,
    DiagnosticSeverity,
    OperationDisposition,
)
from sideloadedipa.errors import DomainError, ErrorCode


def requirement(
    target: str,
    missing_disposition: OperationDisposition,
    *,
    matches: tuple[str, ...] = (),
    kind: AppleResourceKind = AppleResourceKind.BUNDLE_ID,
) -> AppleResourceRequirement:
    return AppleResourceRequirement(
        resource_kind=kind,
        action="ensure",
        target=target,
        bundle_id="io.example.app",
        matching_resource_ids=matches,
        missing_disposition=missing_disposition,
        remediation=f"resolve {target}",
    )


def test_classifies_all_four_dispositions_with_bundle_remediation() -> None:
    requirements = (
        requirement(
            "io.example.app",
            OperationDisposition.SAFE_AUTOMATIC,
            matches=("BUNDLE_ONE",),
        ),
        requirement("APP_GROUPS", OperationDisposition.SAFE_AUTOMATIC),
        requirement("group.io.example", OperationDisposition.MANUAL_REQUIRED),
        requirement("INCREASED_MEMORY_LIMIT", OperationDisposition.BLOCKED),
    )

    plan = plan_apple_resources(
        task_name="Example",
        snapshot_sha256="snapshot-digest",
        requirements=requirements,
    )

    by_target = {operation.target: operation for operation in plan.operations}
    assert by_target["io.example.app"].disposition is OperationDisposition.NO_OP
    assert by_target["io.example.app"].existing_resource_id == "BUNDLE_ONE"
    assert by_target["APP_GROUPS"].disposition is OperationDisposition.SAFE_AUTOMATIC
    assert by_target["group.io.example"].disposition is OperationDisposition.MANUAL_REQUIRED
    assert by_target["INCREASED_MEMORY_LIMIT"].disposition is OperationDisposition.BLOCKED
    for target in ("APP_GROUPS", "group.io.example", "INCREASED_MEMORY_LIMIT"):
        diagnostic = by_target[target].diagnostics[0]
        assert diagnostic.task_name == "Example"
        assert diagnostic.bundle_id == "io.example.app"
        assert diagnostic.remediation == f"resolve {target}"
    assert by_target["APP_GROUPS"].diagnostics[0].severity is DiagnosticSeverity.INFO
    assert by_target["group.io.example"].diagnostics[0].severity is DiagnosticSeverity.WARNING


def test_blocks_ambiguous_and_duplicate_requirements_deterministically() -> None:
    ambiguous = requirement(
        "io.example.ambiguous",
        OperationDisposition.SAFE_AUTOMATIC,
        matches=("BUNDLE_TWO", "BUNDLE_ONE", "BUNDLE_ONE"),
    )
    duplicate = requirement("io.example.duplicate", OperationDisposition.SAFE_AUTOMATIC)

    first = plan_apple_resources(
        task_name="Example",
        snapshot_sha256="snapshot-digest",
        requirements=(duplicate, ambiguous, duplicate),
    )
    second = plan_apple_resources(
        task_name="Example",
        snapshot_sha256="snapshot-digest",
        requirements=(ambiguous, duplicate, duplicate),
    )

    assert first == second
    assert [operation.target for operation in first.operations] == [
        "io.example.ambiguous",
        "io.example.duplicate",
    ]
    assert all(
        operation.disposition is OperationDisposition.BLOCKED for operation in first.operations
    )
    assert first.operations[0].diagnostics[0].code == "apple.resource_ambiguous"
    assert first.operations[0].diagnostics[0].details[-1] == (
        "matching_resource_ids",
        ("BUNDLE_ONE", "BUNDLE_TWO"),
    )
    assert first.operations[1].diagnostics[0].code == "apple.requirement_duplicate"


@pytest.mark.parametrize(
    "invalid",
    [
        replace(requirement("target", OperationDisposition.BLOCKED), action=""),
        replace(requirement("target", OperationDisposition.BLOCKED), target=""),
        replace(
            requirement("target", OperationDisposition.BLOCKED),
            missing_disposition=OperationDisposition.NO_OP,
        ),
        replace(requirement("target", OperationDisposition.BLOCKED), remediation=""),
    ],
)
def test_rejects_invalid_requirements(invalid: AppleResourceRequirement) -> None:
    with pytest.raises(DomainError) as caught:
        plan_apple_resources(
            task_name="Example",
            snapshot_sha256="snapshot-digest",
            requirements=(invalid,),
        )

    assert caught.value.code is ErrorCode.DOMAIN_INVARIANT


def test_requires_plan_identity() -> None:
    with pytest.raises(DomainError) as caught:
        plan_apple_resources(task_name="", snapshot_sha256="", requirements=())

    assert caught.value.code is ErrorCode.DOMAIN_INVARIANT


def test_locally_satisfied_requirement_is_no_op_without_resource_id() -> None:
    local = replace(
        requirement("KEYCHAIN_SHARING", OperationDisposition.BLOCKED),
        satisfied_without_resource=True,
    )

    operation = plan_apple_resources(
        task_name="Example",
        snapshot_sha256="snapshot-digest",
        requirements=(local,),
    ).operations[0]

    assert operation.disposition is OperationDisposition.NO_OP
    assert operation.existing_resource_id is None

    with pytest.raises(DomainError):
        plan_apple_resources(
            task_name="Example",
            snapshot_sha256="snapshot-digest",
            requirements=(replace(local, matching_resource_ids=("UNEXPECTED",)),),
        )
