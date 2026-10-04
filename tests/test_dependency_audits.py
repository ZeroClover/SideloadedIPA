from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.check_dependency_audits import (
    AuditGateError,
    ReviewedException,
    load_reviewed_exceptions,
    validate_npm_audit,
)

ROOT = Path(__file__).parents[1]


def exception(*, advisory: str = "GHSA-f88m-g3jw-g9cj") -> ReviewedException:
    return ReviewedException(
        advisory=advisory,
        package="sharp",
        severity="high",
        affected_dependency_path="next > sharp",
        reachability="The supported route does not invoke the vulnerable decoder.",
        owner="@owner",
        remediation_condition="Upgrade when the parent permits the fixed release.",
        expires_on=date(2026, 8, 23),
    )


def audit_report(*, advisory: str = "GHSA-f88m-g3jw-g9cj") -> dict[str, object]:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "next": {"severity": "high", "via": ["sharp"]},
            "sharp": {
                "severity": "high",
                "via": [
                    {
                        "name": "sharp",
                        "dependency": "sharp",
                        "severity": "high",
                        "url": f"https://github.com/advisories/{advisory}",
                    }
                ],
            },
        },
    }


def exception_document() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "npm": [
            {
                "advisory": "GHSA-f88m-g3jw-g9cj",
                "package": "sharp",
                "severity": "high",
                "affectedDependencyPath": "next > sharp",
                "reachability": "The supported route does not invoke the vulnerable decoder.",
                "owner": "@owner",
                "remediationCondition": "Upgrade when the parent permits the fixed release.",
                "expiresOn": "2026-08-23",
            }
        ],
    }


def test_current_dependency_policy_has_no_exceptions() -> None:
    assert load_reviewed_exceptions(ROOT / ".github" / "dependency-audit-exceptions.json") == ()


def test_reviewed_exception_loader_accepts_complete_unexpired_fixture(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps(exception_document()))
    assert load_reviewed_exceptions(path, today=date(2026, 7, 23)) == (exception(),)


def test_exact_reviewed_advisory_is_accepted() -> None:
    assert validate_npm_audit(audit_report(), (exception(),))[0].package == "sharp"


def test_unreviewed_blocking_advisory_is_rejected() -> None:
    with pytest.raises(AuditGateError, match="unreviewed blocking npm advisories"):
        validate_npm_audit(audit_report(advisory="GHSA-aaaa-bbbb-cccc"), (exception(),))


def test_stale_exception_is_rejected_after_finding_disappears() -> None:
    with pytest.raises(AuditGateError, match="no longer match"):
        validate_npm_audit({"vulnerabilities": {}}, (exception(),))


def test_exception_package_must_match_the_audit() -> None:
    reviewed = exception()
    mismatched = ReviewedException(
        reviewed.advisory,
        "next",
        reviewed.severity,
        reviewed.affected_dependency_path,
        reviewed.reachability,
        reviewed.owner,
        reviewed.remediation_condition,
        reviewed.expires_on,
    )
    with pytest.raises(AuditGateError, match="audit names sharp"):
        validate_npm_audit(audit_report(), (mismatched,))


def test_exception_loader_rejects_missing_review_fields(tmp_path: Path) -> None:
    document = exception_document()
    del document["npm"][0]["owner"]
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps(document))

    with pytest.raises(AuditGateError, match=r"missing=\['owner'\]"):
        load_reviewed_exceptions(path, today=date(2026, 7, 23))


def test_exception_loader_rejects_expiry(tmp_path: Path) -> None:
    document = exception_document()
    document["npm"][0]["expiresOn"] = "2026-07-22"
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps(document))

    with pytest.raises(AuditGateError, match="expired"):
        load_reviewed_exceptions(path, today=date(2026, 7, 23))
