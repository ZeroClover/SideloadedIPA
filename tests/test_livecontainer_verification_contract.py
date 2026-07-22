"""LiveContainer bundle-specific verification contracts."""

from __future__ import annotations

import hashlib
import plistlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    ProfileType,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
    normalize_entitlements,
    thaw_json,
)
from sideloadedipa.verification import (
    EntitlementRepresentationEvidence,
    SignedArtifactEntitlementEvidence,
    SignedEntitlementSliceEvidence,
    SignedNodeEntitlementEvidence,
    verify_three_way_entitlements,
)

TEAM_ID = "TEAMID1234"
APP_ID_PREFIX = "TEAMID1234."
APP_GROUP = "group.io.zeroclover.app.livecontainer"
SIDESTORE_APP_GROUP = "group.io.zeroclover.app.livecontainer.sidestore"
ROOT = PurePosixPath("Payload/LiveContainer.app")
NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
TARGETS = {
    "root": "io.zeroclover.app.livecontainer",
    "process": "io.zeroclover.app.livecontainer.LiveProcess",
    "launch": "io.zeroclover.app.livecontainer.LaunchAppExtension",
    "share": "io.zeroclover.app.livecontainer.ShareExtension",
}
SIDESTORE_TARGETS = {
    "root": "io.zeroclover.app.livecontainer.sidestore",
    "process": "io.zeroclover.app.livecontainer.sidestore.LiveProcess",
    "launch": "io.zeroclover.app.livecontainer.sidestore.LaunchAppExtension",
    "share": "io.zeroclover.app.livecontainer.sidestore.ShareExtension",
    "widget": "io.zeroclover.app.livecontainer.sidestore.LiveWidget",
}
PATHS = {
    "root": ROOT,
    "process": ROOT / "PlugIns/LiveProcess.appex",
    "launch": ROOT / "PlugIns/LaunchAppExtension.appex",
    "share": ROOT / "PlugIns/ShareExtension.appex",
    "widget": ROOT / "PlugIns/LiveWidgetExtension.appex",
}
EXECUTABLES = {
    "root": ROOT / "LiveContainer",
    "process": PATHS["process"] / "LiveProcess",
    "launch": PATHS["launch"] / "LaunchAppExtension",
    "share": PATHS["share"] / "ShareExtension",
    "widget": PATHS["widget"] / "LiveWidgetExtension",
}
SENSITIVE_KEYS = frozenset(
    {
        "com.apple.developer.healthkit",
        "com.apple.developer.healthkit.access",
        "com.apple.developer.healthkit.background-delivery",
        "com.apple.developer.kernel.increased-memory-limit",
        "keychain-access-groups",
    }
)


def keychain_groups() -> list[str]:
    base = f"{APP_ID_PREFIX}com.kdt.livecontainer.shared"
    return [base, *(f"{base}.{index}" for index in range(1, 128))]


def expected_entitlements(
    role: str,
    *,
    targets: Mapping[str, str] = TARGETS,
    app_group: str = APP_GROUP,
) -> dict[str, object]:
    values: dict[str, object] = {
        "application-identifier": f"{APP_ID_PREFIX}{targets[role]}",
        "com.apple.developer.team-identifier": TEAM_ID,
        "com.apple.security.application-groups": [app_group],
        "get-task-allow": True,
    }
    if role in {"root", "process"}:
        values.update(
            {
                "com.apple.developer.healthkit": True,
                "com.apple.developer.healthkit.access": ["health-records"],
                "com.apple.developer.healthkit.background-delivery": True,
                "com.apple.developer.kernel.increased-memory-limit": True,
                "keychain-access-groups": keychain_groups(),
            }
        )
    return values


def _representation(values: Mapping[str, object]) -> EntitlementRepresentationEvidence:
    normalized = normalize_entitlements(values)
    raw = plistlib.dumps(dict(values), sort_keys=True)
    return EntitlementRepresentationEvidence(
        normalized.values,
        normalized.sha256,
        hashlib.sha256(raw).hexdigest(),
    )


def livecontainer_plan(*, sidestore: bool = False) -> SigningPlan:
    roles = (
        "process",
        "launch",
        "share",
        *(("widget",) if sidestore else ()),
        "root",
    )
    targets = SIDESTORE_TARGETS if sidestore else TARGETS
    app_group = SIDESTORE_APP_GROUP if sidestore else APP_GROUP
    nodes = []
    for order, role in enumerate(roles):
        expected = normalize_entitlements(
            expected_entitlements(role, targets=targets, app_group=app_group)
        )
        nodes.append(
            SigningNodePlan(
                PATHS[role],
                EXECUTABLES[role],
                BundleNodeKind.APP if role == "root" else BundleNodeKind.APP_EXTENSION,
                order,
                targets[role],
                f"PROFILE_{role.upper()}",
                PurePosixPath(f"LiveContainer/{targets[role]}.mobileprovision"),
                hashlib.sha256(role.encode()).hexdigest(),
                expected.values,
                expected.sha256,
            )
        )
    return SigningPlan(
        "LiveContainer",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        SigningBackendIdentity("zsign", "1.1.1+sideloadedipa.2", "d" * 64, "1"),
        tuple(nodes),
        "e" * 64,
    )


def profiles(plan: SigningPlan) -> tuple[ProvisioningProfile, ...]:
    result = []
    for node in plan.nodes:
        assert node.target_bundle_id is not None
        assert node.profile_resource_id is not None
        profile_values = {key: thaw_json(value) for key, value in node.expected_entitlements}
        if "keychain-access-groups" in profile_values:
            profile_values["keychain-access-groups"] = [f"{APP_ID_PREFIX}*"]
        normalized = normalize_entitlements(profile_values)
        result.append(
            ProvisioningProfile(
                node.profile_resource_id,
                f"LiveContainer {node.target_bundle_id.rsplit('.', maxsplit=1)[-1]} Dev",
                ProfileType.IOS_APP_DEVELOPMENT,
                node.target_bundle_id,
                f"{APP_ID_PREFIX}{node.target_bundle_id}",
                TEAM_ID,
                "c" * 64,
                ("device",),
                NOW,
                NOW + timedelta(days=90),
                node.profile_sha256 or "",
                node.profile_path or PurePosixPath("missing"),
                normalized.values,
            )
        )
    return tuple(result)


def artifact(
    plan: SigningPlan,
    overrides: Mapping[PurePosixPath, Mapping[str, object]] | None = None,
) -> SignedArtifactEntitlementEvidence:
    documents = overrides or {}
    nodes = []
    for node in plan.nodes:
        values = documents.get(
            node.source_path,
            {key: thaw_json(value) for key, value in node.expected_entitlements},
        )
        representation = _representation(values)
        nodes.append(
            SignedNodeEntitlementEvidence(
                node.source_path,
                node.executable_path,
                hashlib.sha256(node.executable_path.as_posix().encode()).hexdigest(),
                (SignedEntitlementSliceEvidence("ARM64", representation, representation),),
            )
        )
    return SignedArtifactEntitlementEvidence(plan.plan_sha256, "f" * 64, tuple(nodes))


def failed_checks(findings: tuple[VerificationFinding, ...]) -> set[tuple[PurePosixPath, str]]:
    return {(finding.node_path, finding.check) for finding in findings if not finding.passed}


def test_standard_variant_has_four_distinct_profiles_identifiers_and_contracts() -> None:
    plan = livecontainer_plan()
    planned_profiles = profiles(plan)

    findings = verify_three_way_entitlements(plan, planned_profiles, artifact(plan))

    assert len(plan.nodes) == 4
    assert len({node.target_bundle_id for node in plan.nodes}) == 4
    assert len({node.profile_resource_id for node in plan.nodes}) == 4
    assert len({profile.resource_id for profile in planned_profiles}) == 4
    assert all(finding.passed for finding in findings)

    documents = {
        node.source_path: {key: thaw_json(value) for key, value in node.expected_entitlements}
        for node in plan.nodes
    }
    for role in ("root", "process"):
        assert SENSITIVE_KEYS <= documents[PATHS[role]].keys()
        groups = documents[PATHS[role]]["keychain-access-groups"]
        assert isinstance(groups, list)
        assert len(groups) == 128
        assert set(groups) == set(keychain_groups())
    for role in ("launch", "share"):
        assert documents[PATHS[role]]["com.apple.security.application-groups"] == [APP_GROUP]
        assert not (SENSITIVE_KEYS & documents[PATHS[role]].keys())


@pytest.mark.parametrize("role", ["root", "process"])
def test_losing_one_keychain_group_fails_sensitive_contract(role: str) -> None:
    plan = livecontainer_plan()
    signed = expected_entitlements(role)
    signed["keychain-access-groups"] = keychain_groups()[:-1]

    findings = verify_three_way_entitlements(
        plan,
        profiles(plan),
        artifact(plan, {PATHS[role]: signed}),
    )

    assert (PATHS[role], "signed-entitlements:ARM64:xml") in failed_checks(findings)
    assert (PATHS[role], "signed-entitlements:ARM64:der") in failed_checks(findings)


@pytest.mark.parametrize("role", ["launch", "share"])
def test_app_group_only_extensions_reject_inherited_root_entitlements(role: str) -> None:
    plan = livecontainer_plan()
    signed = expected_entitlements(role)
    signed["com.apple.developer.healthkit"] = True

    findings = verify_three_way_entitlements(
        plan,
        profiles(plan),
        artifact(plan, {PATHS[role]: signed}),
    )

    assert (PATHS[role], "signed-entitlements:ARM64:xml") in failed_checks(findings)
    assert (PATHS[role], "signed-entitlements:ARM64:der") in failed_checks(findings)


def test_wrong_profile_or_target_identity_fails_its_bundle() -> None:
    plan = livecontainer_plan()
    planned_profiles = profiles(plan)
    process_node = next(node for node in plan.nodes if node.source_path == PATHS["process"])
    wrong_profile = replace(
        planned_profiles[0],
        resource_id=process_node.profile_resource_id or "",
        application_identifier=f"{APP_ID_PREFIX}{TARGETS['share']}",
        entitlements=normalize_entitlements(expected_entitlements("share")).values,
    )

    findings = verify_three_way_entitlements(
        plan,
        (*planned_profiles[1:], wrong_profile),
        artifact(plan),
    )

    assert (PATHS["process"], "profile-entitlement-authorization") in failed_checks(findings)


def test_sidestore_variant_requires_an_independent_widget_profile_and_policy() -> None:
    plan = livecontainer_plan(sidestore=True)
    planned_profiles = profiles(plan)
    widget = next(node for node in plan.nodes if node.source_path == PATHS["widget"])
    widget_document = {key: thaw_json(value) for key, value in widget.expected_entitlements}

    findings = verify_three_way_entitlements(plan, planned_profiles, artifact(plan))

    assert len(plan.nodes) == 5
    assert len({node.target_bundle_id for node in plan.nodes}) == 5
    assert len({node.profile_resource_id for node in plan.nodes}) == 5
    assert widget.target_bundle_id == SIDESTORE_TARGETS["widget"]
    assert widget.profile_resource_id == "PROFILE_WIDGET"
    assert widget_document["com.apple.security.application-groups"] == [SIDESTORE_APP_GROUP]
    assert not (SENSITIVE_KEYS & widget_document.keys())
    assert all(finding.passed for finding in findings)


def test_sidestore_widget_cannot_reuse_root_policy() -> None:
    plan = livecontainer_plan(sidestore=True)
    copied_root_policy = expected_entitlements(
        "root",
        targets=SIDESTORE_TARGETS,
        app_group=SIDESTORE_APP_GROUP,
    )
    copied_root_policy["application-identifier"] = f"{APP_ID_PREFIX}{SIDESTORE_TARGETS['widget']}"

    findings = verify_three_way_entitlements(
        plan,
        profiles(plan),
        artifact(plan, {PATHS["widget"]: copied_root_policy}),
    )

    assert (PATHS["widget"], "signed-entitlements:ARM64:xml") in failed_checks(findings)
    assert (PATHS["widget"], "signed-entitlements:ARM64:der") in failed_checks(findings)


def test_sidestore_widget_cannot_omit_its_fifth_profile() -> None:
    plan = livecontainer_plan(sidestore=True)
    without_widget = tuple(
        profile for profile in profiles(plan) if profile.resource_id != "PROFILE_WIDGET"
    )

    findings = verify_three_way_entitlements(plan, without_widget, artifact(plan))

    assert (PATHS["widget"], "profile-entitlement-authorization") in failed_checks(findings)
