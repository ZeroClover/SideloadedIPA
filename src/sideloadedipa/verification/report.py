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
    thaw_json,
)

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


def _diagnostic_document(diagnostic: Diagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
        "task_name": diagnostic.task_name,
        "bundle_id": diagnostic.bundle_id,
        "remediation": diagnostic.remediation,
        "details": {key: thaw_json(value) for key, value in diagnostic.details},
    }


def _finding_document(finding: VerificationFinding) -> dict[str, object]:
    return {
        "node_path": finding.node_path.as_posix(),
        "check": finding.check,
        "passed": finding.passed,
        "expected_sha256": finding.expected_sha256,
        "actual_sha256": finding.actual_sha256,
        "diagnostics": [_diagnostic_document(value) for value in finding.diagnostics],
    }


def _canonical_json(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


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

    return hashlib.sha256(_canonical_json(_report_document(plan, result))).hexdigest()


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


def canonical_verification_report_json(plan: SigningPlan, result: VerificationResult) -> bytes:
    """Serialize a validated, schema-versioned, redacted verification report."""

    if result.plan_sha256 != plan.plan_sha256:
        raise ValueError("verification result references a different signing plan")
    derived_gate = verification_publication_gate(plan, result)
    expected_digest = verification_report_sha256(plan, result)
    if result.passed != derived_gate or result.report_sha256 != expected_digest:
        raise ValueError("verification result is inconsistent with its required checks or digest")
    document = _report_document(plan, result)
    document["report_sha256"] = result.report_sha256
    return _canonical_json(document)


def human_verification_report(plan: SigningPlan, result: VerificationResult) -> str:
    """Render every bundle and finding without exposing private signing material."""

    status = "VERIFIED" if verification_publication_gate(plan, result) else "FAILED"
    lines = [
        f"{status}: {plan.task_name}",
        f"Artifact SHA-256: {result.artifact_sha256}",
        f"Report SHA-256: {result.report_sha256}",
    ]
    planned_paths = [node.source_path for node in plan.nodes]
    additional_paths = sorted(
        {finding.node_path for finding in result.findings} - set(planned_paths),
        key=lambda path: path.as_posix(),
    )
    for path in (*planned_paths, *additional_paths):
        node_findings = tuple(finding for finding in result.findings if finding.node_path == path)
        passed = sum(finding.passed for finding in node_findings)
        lines.append(f"{path.as_posix()}: {passed}/{len(node_findings)} checks passed")
        for finding in node_findings:
            check_status = "PASS" if finding.passed else "FAIL"
            lines.append(f"  {check_status} {finding.check}")
            lines.extend(
                f"    {diagnostic.severity.value.upper()} {diagnostic.code}: "
                f"{diagnostic.message}"
                for diagnostic in finding.diagnostics
            )
    return "\n".join(lines)
