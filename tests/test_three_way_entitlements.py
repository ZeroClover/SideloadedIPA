"""Tests for expected/profile/signed entitlement verification."""

from __future__ import annotations

import hashlib
import plistlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from sideloadedipa.domain import (
    BundleNodeKind,
    ProfileType,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
    normalize_entitlements,
)
from sideloadedipa.verification import (
    EntitlementRepresentationEvidence,
    SignedArtifactEntitlementEvidence,
    SignedEntitlementSliceEvidence,
    SignedNodeEntitlementEvidence,
    verify_three_way_entitlements,
)

ROOT = PurePosixPath("Payload/App.app")
NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
EXPECTED = {
    "application-identifier": "TEAMID.io.example.app",
    "com.apple.developer.team-identifier": "TEAMID",
    "keychain-access-groups": ["TEAMID.io.example.app", "TEAMID.shared"],
}


def representation(document: Mapping[str, object]) -> EntitlementRepresentationEvidence:
    normalized = normalize_entitlements(document)
    raw = plistlib.dumps(document, sort_keys=True)
    return EntitlementRepresentationEvidence(
        normalized.values,
        normalized.sha256,
        hashlib.sha256(raw).hexdigest(),
    )


def plan() -> SigningPlan:
    expected = normalize_entitlements(EXPECTED)
    return SigningPlan(
        "Example",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        SigningBackendIdentity("fixture", "1", "d" * 64, "1"),
        (
            SigningNodePlan(
                ROOT,
                ROOT / "App",
                BundleNodeKind.APP,
                0,
                "io.example.app",
                "PROFILE",
                PurePosixPath("Example/profile.mobileprovision"),
                "e" * 64,
                expected.values,
                expected.sha256,
            ),
        ),
        "f" * 64,
    )


def profile() -> ProvisioningProfile:
    allowed = normalize_entitlements(
        {
            **EXPECTED,
            "application-identifier": "TEAMID.*",
            "keychain-access-groups": ["TEAMID.*"],
        }
    )
    return ProvisioningProfile(
        "PROFILE",
        "Example Dev",
        ProfileType.IOS_APP_DEVELOPMENT,
        "io.example.app",
        "TEAMID.io.example.app",
        "TEAMID",
        "c" * 64,
        ("device",),
        NOW,
        NOW + timedelta(days=90),
        "e" * 64,
        PurePosixPath("Example/profile.mobileprovision"),
        allowed.values,
    )


def artifact(
    xml: Mapping[str, object] = EXPECTED,
    der: Mapping[str, object] = EXPECTED,
) -> SignedArtifactEntitlementEvidence:
    return SignedArtifactEntitlementEvidence(
        plan().plan_sha256,
        "0" * 64,
        (
            SignedNodeEntitlementEvidence(
                ROOT,
                ROOT / "App",
                "1" * 64,
                (
                    SignedEntitlementSliceEvidence(
                        "ARM64",
                        representation(xml),
                        representation(der),
                    ),
                ),
            ),
        ),
    )


def failed_checks(findings: tuple[VerificationFinding, ...]) -> list[str]:
    return [value.check for value in findings if not value.passed]


def test_accepts_profile_wildcards_but_requires_exact_signed_values() -> None:
    findings = verify_three_way_entitlements(plan(), (profile(),), artifact())

    assert findings
    assert all(value.passed for value in findings)


def test_detects_profile_denial_and_signed_missing_or_unplanned_values() -> None:
    denied_profile = replace(profile(), entitlements=normalize_entitlements({}).values)
    signed = {**EXPECTED, "unplanned": True}
    signed.pop("com.apple.developer.team-identifier")

    findings = verify_three_way_entitlements(
        plan(),
        (denied_profile,),
        artifact(signed, signed),
    )

    assert "profile-entitlement-authorization" in failed_checks(findings)
    assert "signed-entitlements:ARM64:xml" in failed_checks(findings)
    rendered = repr(findings)
    assert "TEAMID.shared" not in rendered


def test_detects_xml_der_disagreement_and_wrong_team_prefix() -> None:
    wrong = {**EXPECTED, "keychain-access-groups": ["OTHER.shared"]}

    findings = verify_three_way_entitlements(plan(), (profile(),), artifact(EXPECTED, wrong))

    assert "xml-der-entitlements:ARM64" in failed_checks(findings)
    assert any(
        diagnostic.code == "verification.entitlement.team-prefix-mismatch"
        for finding in findings
        for diagnostic in finding.diagnostics
    )


def test_missing_xml_or_der_evidence_fails_the_exact_slice() -> None:
    evidence = artifact()
    node = evidence.nodes[0]
    missing_der = replace(node.slices[0], der=None)

    findings = verify_three_way_entitlements(
        plan(),
        (profile(),),
        replace(evidence, nodes=(replace(node, slices=(missing_der,)),)),
    )

    assert "signed-entitlements:ARM64:der" in failed_checks(findings)
    assert "xml-der-entitlements:ARM64" in failed_checks(findings)


def test_allows_only_exact_explicit_profile_defaults_for_one_node() -> None:
    signed = {**EXPECTED, "get-task-allow": True}
    defaults = {ROOT: {"get-task-allow": True}}

    allowed = verify_three_way_entitlements(
        plan(),
        (replace(profile(), entitlements=representation(signed).values),),
        artifact(signed, signed),
        allowed_profile_defaults=defaults,
    )
    rejected = verify_three_way_entitlements(plan(), (profile(),), artifact(signed, signed))

    assert all(value.passed for value in allowed)
    assert "signed-entitlements:ARM64:xml" in failed_checks(rejected)
