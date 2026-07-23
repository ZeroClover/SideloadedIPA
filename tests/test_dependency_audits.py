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


def test_current_reviewed_exception_is_complete_and_unexpired() -> None:
    reviewed = load_reviewed_exceptions(
        ROOT / ".github" / "dependency-audit-exceptions.json",
        today=date(2026, 7, 23),
    )

    assert reviewed == (
        ReviewedException(
            advisory="GHSA-f88m-g3jw-g9cj",
            package="sharp",
            severity="high",
            affected_dependency_path="next@16.2.11 > sharp@0.34.5",
            reachability=reviewed[0].reachability,
            owner="@ZeroClover",
            remediation_condition=reviewed[0].remediation_condition,
            expires_on=date(2026, 8, 23),
        ),
    )


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
    document = json.loads((ROOT / ".github" / "dependency-audit-exceptions.json").read_text())
    del document["npm"][0]["owner"]
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps(document))

    with pytest.raises(AuditGateError, match=r"missing=\['owner'\]"):
        load_reviewed_exceptions(path, today=date(2026, 7, 23))


def test_exception_loader_rejects_expiry(tmp_path: Path) -> None:
    document = json.loads((ROOT / ".github" / "dependency-audit-exceptions.json").read_text())
    document["npm"][0]["expiresOn"] = "2026-07-22"
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps(document))

    with pytest.raises(AuditGateError, match="expired"):
        load_reviewed_exceptions(path, today=date(2026, 7, 23))
