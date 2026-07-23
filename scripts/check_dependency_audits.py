"""Validate the locked npm audit against narrow, expiring exceptions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NoReturn, cast

_ADVISORY_PATTERN = re.compile(r"^(?:GHSA-[0-9a-z-]+|CVE-\d{4}-\d+)$")
_BLOCKING_SEVERITIES = frozenset({"high", "critical"})


class AuditGateError(ValueError):
    """Raised when audit evidence or an exception is invalid."""


@dataclass(frozen=True, slots=True)
class ReviewedException:
    advisory: str
    package: str
    severity: str
    affected_dependency_path: str
    reachability: str
    owner: str
    remediation_condition: str
    expires_on: date


@dataclass(frozen=True, slots=True)
class AuditFinding:
    advisory: str
    package: str
    severity: str


def _fail(message: str) -> NoReturn:
    raise AuditGateError(message)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return cast(dict[str, object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-empty string")
    return value.strip()


def load_reviewed_exceptions(
    path: Path, *, today: date | None = None
) -> tuple[ReviewedException, ...]:
    """Load and validate advisory-specific npm exceptions."""

    try:
        decoded: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise AuditGateError("dependency audit exception file is unreadable") from error
    root = _mapping(decoded, "exception document")
    if root.get("schemaVersion") != 1:
        _fail("exception document schemaVersion must be 1")
    entries = root.get("npm")
    if not isinstance(entries, list):
        _fail("exception document npm field must be an array")

    current_date = today or datetime.now(timezone.utc).date()
    expected_fields = {
        "advisory",
        "package",
        "severity",
        "affectedDependencyPath",
        "reachability",
        "owner",
        "remediationCondition",
        "expiresOn",
    }
    reviewed: list[ReviewedException] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, f"npm[{index}]")
        if set(entry) != expected_fields:
            missing = sorted(expected_fields - set(entry))
            unexpected = sorted(set(entry) - expected_fields)
            _fail(
                f"npm[{index}] fields do not match the reviewed schema; "
                f"missing={missing}, unexpected={unexpected}"
            )
        advisory = _string(entry["advisory"], f"npm[{index}].advisory")
        if not _ADVISORY_PATTERN.fullmatch(advisory):
            _fail(f"npm[{index}].advisory must be a GHSA or CVE identifier")
        if advisory in seen:
            _fail(f"duplicate npm exception for {advisory}")
        seen.add(advisory)
        severity = _string(entry["severity"], f"npm[{index}].severity").lower()
        if severity not in _BLOCKING_SEVERITIES:
            _fail(f"npm[{index}].severity must be high or critical")
        expiry_text = _string(entry["expiresOn"], f"npm[{index}].expiresOn")
        try:
            expires_on = date.fromisoformat(expiry_text)
        except ValueError as error:
            raise AuditGateError(f"npm[{index}].expiresOn must use YYYY-MM-DD") from error
        if expires_on < current_date:
            _fail(f"npm exception {advisory} expired on {expires_on.isoformat()}")
        reviewed.append(
            ReviewedException(
                advisory=advisory,
                package=_string(entry["package"], f"npm[{index}].package"),
                severity=severity,
                affected_dependency_path=_string(
                    entry["affectedDependencyPath"],
                    f"npm[{index}].affectedDependencyPath",
                ),
                reachability=_string(entry["reachability"], f"npm[{index}].reachability"),
                owner=_string(entry["owner"], f"npm[{index}].owner"),
                remediation_condition=_string(
                    entry["remediationCondition"],
                    f"npm[{index}].remediationCondition",
                ),
                expires_on=expires_on,
            )
        )
    return tuple(reviewed)


def _blocking_findings(report: Mapping[str, object]) -> dict[str, AuditFinding]:
    raw_vulnerabilities = report.get("vulnerabilities")
    vulnerabilities = _mapping(raw_vulnerabilities, "npm audit vulnerabilities")
    findings: dict[str, AuditFinding] = {}

    def collect(package_name: str, trail: frozenset[str]) -> set[str]:
        if package_name in trail:
            _fail(f"npm audit contains a cyclic vulnerability path at {package_name}")
        vulnerability = _mapping(
            vulnerabilities.get(package_name), f"npm vulnerability {package_name}"
        )
        raw_via = vulnerability.get("via")
        if not isinstance(raw_via, list):
            _fail(f"npm vulnerability {package_name}.via must be an array")
        advisory_ids: set[str] = set()
        for index, cause in enumerate(raw_via):
            if isinstance(cause, str):
                advisory_ids.update(collect(cause, trail | {package_name}))
                continue
            detail = _mapping(cause, f"npm vulnerability {package_name}.via[{index}]")
            severity = _string(
                detail.get("severity"), f"npm vulnerability {package_name}.via[{index}].severity"
            ).lower()
            if severity not in _BLOCKING_SEVERITIES:
                continue
            advisory = (
                _string(detail.get("url"), f"npm vulnerability {package_name}.via[{index}].url")
                .rstrip("/")
                .rsplit("/", maxsplit=1)[-1]
            )
            if not _ADVISORY_PATTERN.fullmatch(advisory):
                _fail(f"blocking npm finding for {package_name} lacks a GHSA or CVE identity")
            dependency = _string(
                detail.get("dependency") or detail.get("name"),
                f"npm vulnerability {package_name}.via[{index}].dependency",
            )
            finding = AuditFinding(advisory, dependency, severity)
            previous = findings.get(advisory)
            if previous is not None and previous != finding:
                _fail(f"npm advisory {advisory} has conflicting audit details")
            findings[advisory] = finding
            advisory_ids.add(advisory)
        return advisory_ids

    for package_name, raw_vulnerability in vulnerabilities.items():
        vulnerability = _mapping(raw_vulnerability, f"npm vulnerability {package_name}")
        severity = _string(
            vulnerability.get("severity"), f"npm vulnerability {package_name}.severity"
        ).lower()
        if severity in _BLOCKING_SEVERITIES and not collect(package_name, frozenset()):
            _fail(f"blocking npm finding for {package_name} has no reviewable advisory identity")
    return findings


def validate_npm_audit(
    report: Mapping[str, object], reviewed: Sequence[ReviewedException]
) -> tuple[AuditFinding, ...]:
    """Require every blocking advisory to match one current reviewed exception."""

    findings = _blocking_findings(report)
    exceptions = {item.advisory: item for item in reviewed}
    unexpected = sorted(set(findings) - set(exceptions))
    if unexpected:
        _fail(f"unreviewed blocking npm advisories: {', '.join(unexpected)}")
    stale = sorted(set(exceptions) - set(findings))
    if stale:
        _fail(f"reviewed npm exceptions no longer match the audit: {', '.join(stale)}")
    for advisory, finding in findings.items():
        exception = exceptions[advisory]
        if exception.package != finding.package:
            _fail(
                f"npm exception {advisory} names {exception.package}, "
                f"but the audit names {finding.package}"
            )
        if exception.severity != finding.severity:
            _fail(
                f"npm exception {advisory} severity {exception.severity} "
                f"does not match {finding.severity}"
            )
    return tuple(findings[key] for key in sorted(findings))


def run_npm_audit(project_directory: Path) -> Mapping[str, object]:
    """Run npm's lock-only audit and return its JSON document."""

    command = (
        "npm",
        "audit",
        "--package-lock-only",
        "--audit-level=high",
        "--json",
    )
    completed = subprocess.run(
        command,
        cwd=project_directory,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        _fail(f"npm audit failed to execute (exit {completed.returncode})")
    try:
        decoded: object = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AuditGateError("npm audit did not return valid JSON") from error
    report = _mapping(decoded, "npm audit report")
    if report.get("auditReportVersion") != 2:
        _fail("npm audit report version is unsupported")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path("web"))
    parser.add_argument(
        "--exceptions",
        type=Path,
        default=Path(".github/dependency-audit-exceptions.json"),
    )
    args = parser.parse_args(argv)
    try:
        reviewed = load_reviewed_exceptions(args.exceptions)
        findings = validate_npm_audit(run_npm_audit(args.project), reviewed)
    except AuditGateError as error:
        print(f"dependency audit gate failed: {error}", file=sys.stderr)
        return 1
    print(f"npm audit gate accepted {len(findings)} reviewed blocking advisory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
