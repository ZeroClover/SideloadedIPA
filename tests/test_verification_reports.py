"""Tests for fail-closed verification reports and publication gating."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    Diagnostic,
    DiagnosticSeverity,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
)
from sideloadedipa.verification import (
    VERIFICATION_REPORT_SCHEMA_VERSION,
    build_verification_result,
    canonical_verification_report_json,
    human_verification_report,
    required_verification_checks,
    verification_publication_gate,
)


def _plan() -> SigningPlan:
    return SigningPlan(
        task_name="Example",
        source_ipa_sha256="a" * 64,
        graph_sha256="b" * 64,
        certificate_sha256="c" * 64,
        backend=SigningBackendIdentity("fixture", "1", "d" * 64, "1"),
        nodes=(
            SigningNodePlan(
                PurePosixPath("Payload/Example.app/Frameworks/Kit.framework"),
                PurePosixPath("Payload/Example.app/Frameworks/Kit.framework/Kit"),
                BundleNodeKind.FRAMEWORK,
                0,
                None,
                None,
                None,
                None,
                (),
                "e" * 64,
            ),
            SigningNodePlan(
                PurePosixPath("Payload/Example.app/PlugIns/Share.appex"),
                PurePosixPath("Payload/Example.app/PlugIns/Share.appex/Share"),
                BundleNodeKind.APP_EXTENSION,
                1,
                "io.example.app.Share",
                "PROFILE_SHARE",
                PurePosixPath("profiles/share.mobileprovision"),
                "f" * 64,
                (("application-identifier", "TEAM.io.example.app.Share"),),
                "1" * 64,
            ),
            SigningNodePlan(
                PurePosixPath("Payload/Example.app"),
                PurePosixPath("Payload/Example.app/Example"),
                BundleNodeKind.APP,
                2,
                "io.example.app",
                "PROFILE_ROOT",
                PurePosixPath("profiles/root.mobileprovision"),
                "2" * 64,
                (("application-identifier", "TEAM.io.example.app"),),
                "3" * 64,
            ),
        ),
        plan_sha256="4" * 64,
    )


def _passing_findings(plan: SigningPlan) -> tuple[VerificationFinding, ...]:
    return tuple(
        VerificationFinding(path, check.replace("*", "arm64"), True, "a" * 64, "a" * 64)
        for path, check in required_verification_checks(plan)
    )


def test_builds_stable_redacted_json_and_human_reports() -> None:
    plan = _plan()
    result = build_verification_result(plan, "9" * 64, _passing_findings(plan))

    assert result.passed
    assert verification_publication_gate(plan, result)
    first = canonical_verification_report_json(plan, result)
    assert first == canonical_verification_report_json(plan, result)
    document = json.loads(first)
    assert document["schema_version"] == VERIFICATION_REPORT_SCHEMA_VERSION
    assert document["verified"] is True
    assert document["publication_allowed"] is True
    assert document["report_sha256"] == result.report_sha256
    assert {item["node_path"] for item in document["findings"]} == {
        node.source_path.as_posix() for node in plan.nodes
    }
    assert "PRIVATE" not in first.decode()

    human = human_verification_report(plan, result)
    assert human.startswith("VERIFIED: Example")
    assert all(node.source_path.as_posix() in human for node in plan.nodes)


def test_missing_required_check_fails_closed_and_is_reported() -> None:
    plan = _plan()
    findings = tuple(
        finding
        for finding in _passing_findings(plan)
        if not (
            finding.node_path == PurePosixPath("Payload/Example.app/PlugIns/Share.appex")
            and finding.check == "signed-entitlements:arm64:der"
        )
    )

    result = build_verification_result(plan, "9" * 64, findings)

    assert not result.passed
    assert not verification_publication_gate(plan, result)
    missing = next(
        finding
        for finding in result.findings
        if finding.check == "required-check:signed-entitlements:*:der"
    )
    assert not missing.passed
    assert missing.diagnostics[0].code == "verification.required_check_missing"
    assert "FAIL required-check:signed-entitlements:*:der" in human_verification_report(
        plan, result
    )


def test_failed_supplemental_finding_blocks_publication() -> None:
    plan = _plan()
    failed = VerificationFinding(
        PurePosixPath("Payload/Example.app"),
        "independent-oracle",
        False,
        diagnostics=(
            Diagnostic(
                "verification.oracle",
                DiagnosticSeverity.ERROR,
                "independent verifier disagreed",
            ),
        ),
    )

    result = build_verification_result(plan, "9" * 64, (*_passing_findings(plan), failed))

    assert not result.passed
    assert not verification_publication_gate(plan, result)
    human = human_verification_report(plan, result)
    assert "FAIL independent-oracle" in human
    assert "ERROR verification.oracle: independent verifier disagreed" in human


def test_duplicate_evidence_fails_closed() -> None:
    plan = _plan()
    findings = _passing_findings(plan)

    result = build_verification_result(plan, "9" * 64, (*findings, findings[0]))

    assert not result.passed
    assert any(
        finding.diagnostics
        and finding.diagnostics[0].code == "verification.required_check_duplicate"
        for finding in result.findings
    )


def test_gate_does_not_trust_stored_boolean_or_report_digest() -> None:
    plan = _plan()
    incomplete = build_verification_result(plan, "9" * 64, ())
    forged = replace(incomplete, passed=True)

    assert not verification_publication_gate(plan, forged)
    with pytest.raises(ValueError, match="inconsistent"):
        canonical_verification_report_json(plan, forged)

    complete = build_verification_result(plan, "9" * 64, _passing_findings(plan))
    with pytest.raises(ValueError, match="inconsistent"):
        canonical_verification_report_json(plan, replace(complete, passed=False))
    with pytest.raises(ValueError, match="inconsistent"):
        canonical_verification_report_json(plan, replace(complete, report_sha256="0" * 64))


def test_human_report_includes_failed_unplanned_node() -> None:
    plan = _plan()
    unknown = VerificationFinding(
        PurePosixPath("Payload/Example.app/PlugIns/Unknown.appex"),
        "unplanned-entitlement-evidence",
        False,
    )
    result = build_verification_result(plan, "9" * 64, (*_passing_findings(plan), unknown))

    human = human_verification_report(plan, result)
    assert "Payload/Example.app/PlugIns/Unknown.appex: 0/1 checks passed" in human
    assert "FAIL unplanned-entitlement-evidence" in human


def test_artifact_checks_are_bound_to_root_bundle_even_when_plan_is_deepest_first() -> None:
    plan = _plan()

    requirements = required_verification_checks(plan)

    assert (
        PurePosixPath("Payload/Example.app"),
        "protected-payload",
    ) in requirements
    assert (
        PurePosixPath("Payload/Example.app/Frameworks/Kit.framework"),
        "protected-payload",
    ) not in requirements


def test_gate_rejects_a_result_for_another_plan() -> None:
    plan = _plan()
    result = build_verification_result(plan, "9" * 64, _passing_findings(plan))

    assert not verification_publication_gate(replace(plan, plan_sha256="8" * 64), result)
