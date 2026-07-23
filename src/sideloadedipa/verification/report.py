"""Canonical verification reports and the publication decision."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import PurePosixPath
from typing import cast

from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    FrozenJsonObject,
    SigningPlan,
    VerificationFinding,
    VerificationResult,
    freeze_json,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
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


def canonical_verification_report_json(
    plan: SigningPlan,
    result: VerificationResult,
) -> bytes:
    """Serialize the digest-bound canonical verification evidence."""

    if result.report_sha256 != verification_report_sha256(plan, result):
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "verification report digest is inconsistent with its findings",
            task_name=plan.task_name,
        )
    document = _report_document(plan, result)
    document["report_sha256"] = result.report_sha256
    return canonical_json(document)


def _parse_error(plan: SigningPlan, message: str) -> ConfigurationError:
    return ConfigurationError(
        ErrorCode.CONFIG_INVALID,
        message,
        task_name=plan.task_name,
        remediation="discard the retained verification report and rerun verification",
    )


def parse_verification_report_json(
    plan: SigningPlan,
    payload: bytes,
) -> VerificationResult:
    """Decode canonical verification evidence and verify every digest-bound field."""

    try:
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise TypeError
        findings_document = document["findings"]
        if not isinstance(findings_document, list):
            raise TypeError
        findings: list[VerificationFinding] = []
        for finding_document in findings_document:
            if not isinstance(finding_document, dict):
                raise TypeError
            diagnostics_document = finding_document["diagnostics"]
            if not isinstance(diagnostics_document, list):
                raise TypeError
            diagnostics: list[Diagnostic] = []
            for diagnostic_value in diagnostics_document:
                if not isinstance(diagnostic_value, dict) or not isinstance(
                    diagnostic_value.get("details"), dict
                ):
                    raise TypeError
                details = freeze_json(diagnostic_value["details"])
                if not isinstance(details, FrozenJsonObject):
                    raise TypeError
                diagnostics.append(
                    Diagnostic(
                        code=cast(str, diagnostic_value["code"]),
                        severity=DiagnosticSeverity(cast(str, diagnostic_value["severity"])),
                        message=cast(str, diagnostic_value["message"]),
                        task_name=cast(str | None, diagnostic_value["task_name"]),
                        bundle_id=cast(str | None, diagnostic_value["bundle_id"]),
                        remediation=cast(str | None, diagnostic_value["remediation"]),
                        details=details.items,
                    )
                )
            findings.append(
                VerificationFinding(
                    node_path=PurePosixPath(cast(str, finding_document["node_path"])),
                    check=cast(str, finding_document["check"]),
                    passed=cast(bool, finding_document["passed"]),
                    expected_sha256=cast(
                        str | None,
                        finding_document["expected_sha256"],
                    ),
                    actual_sha256=cast(
                        str | None,
                        finding_document["actual_sha256"],
                    ),
                    diagnostics=tuple(diagnostics),
                )
            )
        result = VerificationResult(
            plan_sha256=cast(str, document["plan_sha256"]),
            artifact_sha256=cast(str, document["artifact_sha256"]),
            passed=cast(bool, document["verified"]),
            findings=tuple(findings),
            report_sha256=cast(str, document["report_sha256"]),
        )
        if (
            document["schema_version"] != VERIFICATION_REPORT_SCHEMA_VERSION
            or document["task_name"] != plan.task_name
            or document["publication_allowed"] is not result.passed
            or not isinstance(result.passed, bool)
            or not isinstance(result.plan_sha256, str)
            or not isinstance(result.artifact_sha256, str)
            or not isinstance(result.report_sha256, str)
            or not result.report_sha256
        ):
            raise TypeError
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise _parse_error(plan, "verification report fields are invalid") from error
    try:
        canonical = canonical_verification_report_json(plan, result)
    except DomainError as error:
        raise _parse_error(plan, "verification report digest is invalid") from error
    if canonical != canonical_json(document):
        raise _parse_error(plan, "verification report canonical evidence is inconsistent")
    return result


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
