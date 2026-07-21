"""Three-way expected, profile, and signed entitlement verification."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from sideloadedipa.domain import (
    Diagnostic,
    DiagnosticSeverity,
    FrozenJsonValue,
    ProvisioningProfile,
    SigningPlan,
    VerificationFinding,
    normalize_entitlements,
    thaw_json,
)
from sideloadedipa.verification.artifact import (
    EntitlementRepresentationEvidence,
    SignedArtifactEntitlementEvidence,
)
from sideloadedipa.verification.entitlements import (
    EntitlementComparison,
    EntitlementComparisonMode,
    EntitlementDifference,
    EntitlementIdentityContext,
    compare_entitlements,
)


def _document(values: tuple[tuple[str, FrozenJsonValue], ...]) -> dict[str, object]:
    return {key: thaw_json(value) for key, value in values}


def _diagnostics(
    comparison: EntitlementComparison,
    *,
    task_name: str,
    bundle_id: str | None,
) -> tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(
            code=f"verification.entitlement.{difference.reason}",
            severity=DiagnosticSeverity.ERROR,
            message="entitlement semantic comparison failed",
            task_name=task_name,
            bundle_id=bundle_id,
            remediation="correct the profile or signed entitlement document and retry",
            details=(
                ("path", difference.path),
                ("expected_sha256", difference.expected_sha256),
                ("actual_sha256", difference.actual_sha256),
            ),
        )
        for difference in comparison.differences
    )


def _finding(
    path: PurePosixPath,
    check: str,
    comparison: EntitlementComparison,
    *,
    task_name: str,
    bundle_id: str | None,
    expected_sha256: str | None = None,
    actual_sha256: str | None = None,
) -> VerificationFinding:
    return VerificationFinding(
        path,
        check,
        comparison.passed,
        expected_sha256,
        actual_sha256,
        _diagnostics(comparison, task_name=task_name, bundle_id=bundle_id),
    )


def _profile_prefix(profile: ProvisioningProfile, target_bundle_id: str) -> str:
    return profile.application_identifier[: -len(target_bundle_id)]


def _missing_comparison(reason: str) -> EntitlementComparison:
    return EntitlementComparison((EntitlementDifference("$", reason, None, None),))


def _representation_document(
    evidence: EntitlementRepresentationEvidence,
) -> dict[str, object]:
    return _document(evidence.values)


def verify_three_way_entitlements(
    plan: SigningPlan,
    profiles: tuple[ProvisioningProfile, ...],
    artifact: SignedArtifactEntitlementEvidence,
    *,
    allowed_profile_defaults: Mapping[PurePosixPath, Mapping[str, object]] | None = None,
) -> tuple[VerificationFinding, ...]:
    """Compare planned values against profile authorization and signed XML/DER."""

    defaults = allowed_profile_defaults or {}
    profiles_by_id = {value.resource_id: value for value in profiles}
    evidence_by_path = {value.source_path: value for value in artifact.nodes}
    findings: list[VerificationFinding] = []

    for node in plan.nodes:
        expected = _document(node.expected_entitlements)
        expected.update(defaults.get(node.source_path, {}))
        expected_sha256 = normalize_entitlements(expected).sha256
        identity: EntitlementIdentityContext | None = None
        if node.profile_resource_id is not None and node.target_bundle_id is not None:
            profile = profiles_by_id.get(node.profile_resource_id)
            if profile is None:
                comparison = _missing_comparison("missing-profile-evidence")
            else:
                profile_document = _document(profile.entitlements)
                comparison = compare_entitlements(
                    expected,
                    profile_document,
                    mode=EntitlementComparisonMode.PROFILE_AUTHORIZATION,
                )
                identity = EntitlementIdentityContext(
                    profile.team_id,
                    _profile_prefix(profile, node.target_bundle_id),
                    node.target_bundle_id,
                )
            findings.append(
                _finding(
                    node.source_path,
                    "profile-entitlement-authorization",
                    comparison,
                    task_name=plan.task_name,
                    bundle_id=node.target_bundle_id,
                    expected_sha256=expected_sha256,
                )
            )

        evidence = evidence_by_path.get(node.source_path)
        if evidence is None:
            missing = _missing_comparison("missing-signed-evidence")
            findings.append(
                _finding(
                    node.source_path,
                    "signed-entitlements",
                    missing,
                    task_name=plan.task_name,
                    bundle_id=node.target_bundle_id,
                    expected_sha256=expected_sha256,
                )
            )
            continue

        for item in evidence.slices:
            if item.xml is None or item.der is None:
                missing_name = "xml" if item.xml is None else "der"
                findings.append(
                    _finding(
                        node.source_path,
                        f"xml-der-entitlements:{item.architecture}",
                        _missing_comparison(f"missing-signed-{missing_name}-evidence"),
                        task_name=plan.task_name,
                        bundle_id=node.target_bundle_id,
                    )
                )
            else:
                xml_der = compare_entitlements(
                    _representation_document(item.xml),
                    _representation_document(item.der),
                )
                findings.append(
                    _finding(
                        node.source_path,
                        f"xml-der-entitlements:{item.architecture}",
                        xml_der,
                        task_name=plan.task_name,
                        bundle_id=node.target_bundle_id,
                        expected_sha256=item.xml.semantic_sha256,
                        actual_sha256=item.der.semantic_sha256,
                    )
                )
            for representation_name, signed in (("xml", item.xml), ("der", item.der)):
                if signed is None:
                    findings.append(
                        _finding(
                            node.source_path,
                            f"signed-entitlements:{item.architecture}:{representation_name}",
                            _missing_comparison(f"missing-signed-{representation_name}-evidence"),
                            task_name=plan.task_name,
                            bundle_id=node.target_bundle_id,
                            expected_sha256=expected_sha256,
                        )
                    )
                    continue
                signed_comparison = compare_entitlements(
                    expected,
                    _representation_document(signed),
                    identity=identity,
                )
                findings.append(
                    _finding(
                        node.source_path,
                        f"signed-entitlements:{item.architecture}:{representation_name}",
                        signed_comparison,
                        task_name=plan.task_name,
                        bundle_id=node.target_bundle_id,
                        expected_sha256=expected_sha256,
                        actual_sha256=signed.semantic_sha256,
                    )
                )

    for path in sorted(set(evidence_by_path) - {value.source_path for value in plan.nodes}):
        unexpected = compare_entitlements({}, {"unplanned-node": path.as_posix()})
        findings.append(
            _finding(
                path,
                "unplanned-entitlement-evidence",
                unexpected,
                task_name=plan.task_name,
                bundle_id=None,
            )
        )
    return tuple(findings)
