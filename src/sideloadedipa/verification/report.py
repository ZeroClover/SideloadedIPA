"""Canonical verification reports and the publication decision."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import PurePosixPath

from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    SigningPlan,
    VerificationFinding,
    VerificationResult,
)
from sideloadedipa.util.atomics import canonical_json, diagnostic_document

VERIFICATION_REPORT_SCHEMA_VERSION = 1

_ARTIFACT_CHECKS = (
    "source-artifact",
    "safe-output-archive",
    "source-plan-node-set",
    "output-graph-parity",
    "planned-identifiers",
    "executable-set",
    "protected-info-plists",
    "protected-payload",
)
_PROFILE_CHECKS = (
    "bundle-identifier",
    "embedded-profile-sha256",
    "embedded-profile-validation",
    "profile-entitlement-authorization",
    "signed-entitlements:*:xml",
    "signed-entitlements:*:der",
    "xml-der-entitlements:*",
)
_RESOURCE_SEAL_KINDS = frozenset(
    {BundleNodeKind.APP, BundleNodeKind.APP_EXTENSION, BundleNodeKind.FRAMEWORK}
)


def _root_path(plan: SigningPlan) -> PurePosixPath:
    return min(
        (node.source_path for node in plan.nodes),
        key=lambda path: (len(path.parts), path.as_posix()),
    )


def required_verification_checks(
    plan: SigningPlan,
) -> tuple[tuple[PurePosixPath, str], ...]:
    """Return the complete plan-derived verification contract."""

    root = _root_path(plan)
    required = [(root, check) for check in _ARTIFACT_CHECKS]
    for node in plan.nodes:
        required.append((node.source_path, "code-signature"))
        if node.kind in _RESOURCE_SEAL_KINDS:
            required.append((node.source_path, "nested-resource-seal"))
        if node.profile_resource_id is not None:
            required.extend((node.source_path, check) for check in _PROFILE_CHECKS)
    return tuple(sorted(required, key=lambda value: (value[0].as_posix(), value[1])))


def _matches(requirement: str, check: str) -> bool:
    if "*" not in requirement:
        return requirement == check
    prefix, suffix = requirement.split("*", maxsplit=1)
    middle = check.removeprefix(prefix).removesuffix(suffix)
    return check.startswith(prefix) and check.endswith(suffix) and bool(middle)


def _contract_failures(
    plan: SigningPlan,
    findings: tuple[VerificationFinding, ...],
) -> tuple[tuple[PurePosixPath, str, str], ...]:
    counts = Counter((finding.node_path, finding.check) for finding in findings)
    failures: list[tuple[PurePosixPath, str, str]] = []
    for path, requirement in required_verification_checks(plan):
        matches = sum(
            count
            for (finding_path, check), count in counts.items()
            if finding_path == path and _matches(requirement, check)
        )
        if matches == 0:
            failures.append((path, requirement, "missing"))
    for (path, check), count in counts.items():
        if count > 1:
            failures.append((path, check, "duplicate"))
    return tuple(sorted(failures, key=lambda value: (value[0].as_posix(), value[1], value[2])))


def _contract_finding(
    plan: SigningPlan,
    path: PurePosixPath,
    check: str,
    reason: str,
) -> VerificationFinding:
    return VerificationFinding(
        path,
        f"required-check:{check}",
        False,
        diagnostics=(
            Diagnostic(
                code=f"verification.required_check_{reason}",
                severity=DiagnosticSeverity.ERROR,
                message=f"required verification evidence is {reason}",
                task_name=plan.task_name,
                remediation="rerun every required verifier before publication",
                details=(("required_check", check), ("node_path", path.as_posix())),
            ),
        ),
    )


def _finding_document(finding: VerificationFinding) -> dict[str, object]:
    return {
        "node_path": finding.node_path.as_posix(),
        "check": finding.check,
        "passed": finding.passed,
        "expected_sha256": finding.expected_sha256,
        "actual_sha256": finding.actual_sha256,
        "diagnostics": [diagnostic_document(value) for value in finding.diagnostics],
    }


def _report_document(plan: SigningPlan, result: VerificationResult) -> dict[str, object]:
    return {
        "schema_version": VERIFICATION_REPORT_SCHEMA_VERSION,
        "task_name": plan.task_name,
        "plan_sha256": result.plan_sha256,
        "artifact_sha256": result.artifact_sha256,
        "verified": result.passed,
        "publication_allowed": result.passed,
        "required_checks": [
            {"node_path": path.as_posix(), "check": check}
            for path, check in required_verification_checks(plan)
        ],
        "findings": [_finding_document(value) for value in result.findings],
    }


def verification_report_sha256(plan: SigningPlan, result: VerificationResult) -> str:
    """Digest a canonical redacted report without its self-referential digest."""

    return hashlib.sha256(canonical_json(_report_document(plan, result))).hexdigest()


def build_verification_result(
    plan: SigningPlan,
    artifact_sha256: str,
    findings: tuple[VerificationFinding, ...],
) -> VerificationResult:
    """Build a fail-closed result from findings and the plan-derived contract."""

    raw_findings = tuple(
        finding for finding in findings if not finding.check.startswith("required-check:")
    )
    contract_findings = tuple(
        _contract_finding(plan, path, check, reason)
        for path, check, reason in _contract_failures(plan, raw_findings)
    )
    ordered = tuple(
        sorted(
            (*raw_findings, *contract_findings),
            key=lambda value: (value.node_path.as_posix(), value.check),
        )
    )
    passed = bool(ordered) and not contract_findings and all(value.passed for value in ordered)
    partial = VerificationResult(plan.plan_sha256, artifact_sha256, passed, ordered, "")
    return VerificationResult(
        partial.plan_sha256,
        partial.artifact_sha256,
        partial.passed,
        partial.findings,
        verification_report_sha256(plan, partial),
    )


def verification_publication_gate(plan: SigningPlan, result: VerificationResult) -> bool:
    """Return the sole publication decision, derived from required findings only."""

    raw_findings = tuple(
        finding for finding in result.findings if not finding.check.startswith("required-check:")
    )
    return (
        result.plan_sha256 == plan.plan_sha256
        and not _contract_failures(plan, raw_findings)
        and bool(raw_findings)
        and all(finding.passed for finding in raw_findings)
    )
