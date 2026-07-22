"""Tests for fail-closed verification reports and publication gating."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

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
    build_verification_result,
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


def test_builds_verified_result_that_opens_publication_gate() -> None:
    plan = _plan()
    result = build_verification_result(plan, "9" * 64, _passing_findings(plan))

    assert result.passed
    assert verification_publication_gate(plan, result)


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
