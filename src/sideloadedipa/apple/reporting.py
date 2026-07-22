"""Apple resource plan construction and command report rendering."""

from __future__ import annotations

from collections import Counter
from typing import cast

from sideloadedipa.apple.expected_entitlements import requirements_for_task
from sideloadedipa.apple.intents import BundleResourceIntent
from sideloadedipa.apple.planning import plan_apple_resources
from sideloadedipa.application import CommandResult
from sideloadedipa.domain import (
    AppleOperation,
    AppleResourcePlan,
    AppleStateSnapshot,
    CertificateIdentity,
    FrozenJsonObject,
    OperationDisposition,
    Task,
    freeze_json,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.util.atomics import diagnostic_document


def operation_document(operation: AppleOperation) -> dict[str, object]:
    return {
        "disposition": operation.disposition.value,
        "resource_kind": operation.resource_kind.value,
        "action": operation.action,
        "target": operation.target,
        "bundle_id": operation.bundle_id,
        "existing_resource_id": operation.existing_resource_id,
        "diagnostics": [diagnostic_document(value) for value in operation.diagnostics],
    }


def plan_document(
    *,
    command: str,
    apply: bool,
    snapshot: AppleStateSnapshot,
    certificate: CertificateIdentity,
    tasks: tuple[Task, ...],
    intents_by_task: dict[str, tuple[BundleResourceIntent, ...]],
    plans: dict[str, AppleResourcePlan],
    status: str | None = None,
    manifests: dict[str, tuple[str, str]] | None = None,
) -> dict[str, object]:
    counts = Counter(
        operation.disposition.value for plan in plans.values() for operation in plan.operations
    )
    blocked = (
        counts[OperationDisposition.BLOCKED.value]
        + counts[OperationDisposition.MANUAL_REQUIRED.value]
    )
    return {
        "schema_version": 1,
        "command": command,
        "apply": apply,
        "status": status or ("blocked" if blocked else "ready"),
        "snapshot_sha256": snapshot.snapshot_sha256,
        "certificate": {
            "resource_id": certificate.resource_id,
            "team_id": certificate.team_id,
            "certificate_sha256": certificate.certificate_sha256,
            "expires_at": certificate.expires_at.isoformat(),
        },
        "counts": dict(sorted(counts.items())),
        "tasks": [
            {
                "task_name": task.task_name,
                "bundle_count": len(intents_by_task[task.task_name]),
                "operations": [
                    operation_document(operation) for operation in plans[task.task_name].operations
                ],
                "manifest": (
                    {
                        "path": manifests[task.task_name][0],
                        "sha256": manifests[task.task_name][1],
                    }
                    if manifests is not None and task.task_name in manifests
                    else None
                ),
            }
            for task in tasks
        ],
    }


def build_plans(
    tasks: tuple[Task, ...],
    intents_by_task: dict[str, tuple[BundleResourceIntent, ...]],
    snapshot: AppleStateSnapshot,
    certificate: CertificateIdentity,
) -> dict[str, AppleResourcePlan]:
    return {
        task.task_name: plan_apple_resources(
            task_name=task.task_name,
            snapshot_sha256=snapshot.snapshot_sha256,
            requirements=requirements_for_task(
                task,
                intents_by_task[task.task_name],
                snapshot,
                certificate,
            ),
        )
        for task in tasks
    }


def human_report(document: dict[str, object]) -> str:
    counts = cast(dict[str, int], document["counts"])
    lines = [
        (
            f"Apple {document['command']}: {document['status']}; "
            f"{counts.get('no-op', 0)} no-op, "
            f"{counts.get('safe-automatic', 0)} automatic, "
            f"{counts.get('manual-required', 0)} manual, "
            f"{counts.get('blocked', 0)} blocked"
        )
    ]
    for task_document in cast(list[dict[str, object]], document["tasks"]):
        lines.append(f"Task {task_document['task_name']} ({task_document['bundle_count']} bundles)")
        for operation in cast(list[dict[str, object]], task_document["operations"]):
            bundle_suffix = (
                f" [{operation['bundle_id']}]" if operation["bundle_id"] is not None else ""
            )
            lines.append(
                f"  {operation['disposition']}: {operation['resource_kind']} "
                f"{operation['target']}{bundle_suffix}"
            )
            for diagnostic in cast(list[dict[str, object]], operation["diagnostics"]):
                remediation = diagnostic["remediation"]
                if remediation:
                    lines.append(f"    remediation: {remediation}")
        manifest = task_document["manifest"]
        if manifest is not None:
            manifest_document = cast(dict[str, object], manifest)
            lines.append(
                f"  manifest: {manifest_document['path']} " f"sha256={manifest_document['sha256']}"
            )
    return "\n".join(lines)


def command_result(document: dict[str, object]) -> CommandResult:
    frozen = freeze_json(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "Apple command report root must be an object",
            remediation="discard the malformed report and rerun the Apple command",
        )
    return CommandResult(
        exit_code=0 if document["status"] in {"ready", "applied"} else 1,
        human_output=human_report(document),
        payload=frozen.items,
    )
