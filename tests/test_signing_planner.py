"""Tests for the pure immutable signing-plan join."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import cast

import pytest

from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    BundleRule,
    CertificateIdentity,
    EntitlementMode,
    EntitlementPolicy,
    ExpectedNodeEntitlements,
    IdentifierStrategy,
    ProfileManifestEntry,
    ProfileResourceManifest,
    ProfileType,
    ProvisioningProfile,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningPolicy,
    SourceConfig,
    SourceKind,
    Task,
    UnknownProfileBundlePolicy,
    normalize_entitlements,
    reconcile_bundle_rules,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.profile_storage import build_profile_manifest, profile_relative_path
from sideloadedipa.signing_planner import (
    SigningPlanRequest,
    build_signing_plan,
    canonical_signing_plan_json,
)

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def task() -> Task:
    return Task(
        task_name="Example",
        app_name="Example",
        bundle_id="io.example.app",
        source=SourceConfig(SourceKind.DIRECT_URL, "https://example.com/App.ipa"),
        slug="Example",
        signing=SigningPolicy(
            IdentifierStrategy.PRESERVE_SOURCE_SUFFIX,
            UnknownProfileBundlePolicy.ERROR,
            ProfileType.IOS_APP_DEVELOPMENT,
            bundles=(
                BundleRule(
                    "com.upstream.app",
                    EntitlementPolicy(EntitlementMode.PROFILE),
                    role="root",
                ),
                BundleRule(
                    "com.upstream.app.Share",
                    EntitlementPolicy(EntitlementMode.PROFILE),
                ),
            ),
        ),
    )


def node(
    path: str,
    kind: BundleNodeKind,
    *,
    source_bundle_id: str | None = None,
    parent: str | None = None,
) -> BundleNode:
    value = PurePosixPath(path)
    return BundleNode(
        path=value,
        kind=kind,
        depth=len(value.parts),
        executable_path=value / "Executable",
        executable_sha256=hashlib.sha256(path.encode()).hexdigest(),
        parent_path=PurePosixPath(parent) if parent else None,
        source_bundle_id=source_bundle_id,
    )


ROOT = node("Payload/App.app", BundleNodeKind.APP, source_bundle_id="com.upstream.app")
EXTENSION = node(
    "Payload/App.app/PlugIns/Share.appex",
    BundleNodeKind.APP_EXTENSION,
    source_bundle_id="com.upstream.app.Share",
    parent="Payload/App.app",
)
FRAMEWORK = node(
    "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.framework",
    BundleNodeKind.FRAMEWORK,
    parent="Payload/App.app/PlugIns/Share.appex",
)


def profile(target: str, resource_id: str, entitlements: dict[str, object]) -> ProvisioningProfile:
    normalized = normalize_entitlements(entitlements)
    return ProvisioningProfile(
        resource_id=resource_id,
        name=f"{resource_id} Dev",
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        bundle_id=target,
        application_identifier=f"PREFIX.{target}",
        team_id="TEAMID1234",
        certificate_sha256="c" * 64,
        device_ids=("device-hash",),
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
        profile_sha256=hashlib.sha256(resource_id.encode()).hexdigest(),
        path=profile_relative_path("Example", target),
        entitlements=normalized.values,
    )


def manifest(profiles: tuple[ProvisioningProfile, ...]) -> ProfileResourceManifest:
    return build_profile_manifest(
        task_name="Example",
        snapshot_sha256="snapshot",
        entries=tuple(
            ProfileManifestEntry(
                target_bundle_id=value.bundle_id,
                bundle_resource_id=f"BUNDLE_{index}",
                profile_resource_id=value.resource_id,
                certificate_resource_id="CERT_ONE",
                profile_path=value.path,
                profile_sha256=value.profile_sha256,
                device_set_sha256="d" * 64,
                expires_at=value.expires_at,
            )
            for index, value in enumerate(profiles)
        ),
    )


def valid_request() -> SigningPlanRequest:
    configured_task = task()
    graph = BundleGraph(
        root_path=ROOT.path,
        nodes=(ROOT, EXTENSION, FRAMEWORK),
        source_sha256="a" * 64,
        graph_sha256="b" * 64,
    )
    root_entitlements = normalize_entitlements({"application-identifier": "PREFIX.io.example.app"})
    extension_entitlements = normalize_entitlements(
        {"application-identifier": "PREFIX.io.example.app.Share"}
    )
    profiles = (
        profile("io.example.app", "PROFILE_ROOT", dict(root_entitlements.values)),
        profile("io.example.app.Share", "PROFILE_SHARE", dict(extension_entitlements.values)),
    )
    backend = SigningBackendIdentity(
        "zsign",
        "1.1.1+sideloadedipa.1",
        "e" * 64,
        "1",
        (
            SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
            SigningBackendFeature.RECURSIVE_SIGNING,
        ),
    )
    certificate = CertificateIdentity(
        "CERT_ONE",
        "TEAMID1234",
        "1234ABCD",
        "f" * 64,
        "c" * 64,
        NOW + timedelta(days=90),
    )

    return SigningPlanRequest(
        task=configured_task,
        graph=graph,
        policy=reconcile_bundle_rules(configured_task, graph),
        profile_manifest=manifest(profiles),
        profiles=tuple(reversed(profiles)),
        certificate=certificate,
        expected_entitlements=(
            ExpectedNodeEntitlements(
                EXTENSION.path, extension_entitlements.values, extension_entitlements.sha256
            ),
            ExpectedNodeEntitlements(ROOT.path, root_entitlements.values, root_entitlements.sha256),
        ),
        backend=backend,
    )


def test_joins_inventory_policy_profiles_entitlements_certificate_and_backend() -> None:
    request = valid_request()
    graph = request.graph
    certificate = request.certificate
    backend = request.backend

    plan = build_signing_plan(request)
    repeated = build_signing_plan(request)
    document = json.loads(canonical_signing_plan_json(plan))

    assert plan == repeated
    assert plan.source_ipa_sha256 == graph.source_sha256
    assert plan.graph_sha256 == graph.graph_sha256
    assert plan.certificate_sha256 == certificate.certificate_sha256
    assert plan.backend is backend
    assert [node.source_path for node in plan.nodes] == [ROOT.path, EXTENSION.path, FRAMEWORK.path]
    assert [node.target_bundle_id for node in plan.nodes] == [
        "io.example.app",
        "io.example.app.Share",
        None,
    ]
    assert [node.profile_resource_id for node in plan.nodes] == [
        "PROFILE_ROOT",
        "PROFILE_SHARE",
        None,
    ]
    assert plan.nodes[-1].expected_entitlements == ()
    assert plan.nodes[-1].expected_entitlements_sha256 == normalize_entitlements({}).sha256
    assert document["plan_sha256"] == plan.plan_sha256
    without_digest = {key: value for key, value in document.items() if key != "plan_sha256"}
    assert (
        plan.plan_sha256
        == hashlib.sha256(
            json.dumps(without_digest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


@pytest.mark.parametrize("mode", ["missing", "duplicate", "unused"])
def test_rejects_missing_duplicate_and_unused_profiles(mode: str) -> None:
    request = valid_request()
    if mode == "missing":
        profiles = request.profiles[:-1]
    elif mode == "duplicate":
        profiles = (
            *request.profiles,
            replace(request.profiles[0], resource_id="PROFILE_DUPLICATE"),
        )
    else:
        profiles = (
            *request.profiles,
            profile("io.example.unused", "PROFILE_UNUSED", {}),
        )

    with pytest.raises(DomainError) as caught:
        build_signing_plan(replace(request, profiles=profiles))

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
    details = dict(caught.value.safe_details)
    if mode == "unused":
        assert details["unused_profile_ids"] == ("PROFILE_UNUSED",)
    else:
        assert details["conflicting_bundle_ids"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("team_id", "OTHERTEAM"), ("certificate_sha256", "9" * 64)],
)
def test_rejects_profile_team_or_certificate_mismatch(field: str, value: str) -> None:
    request = valid_request()
    changed = replace(request.profiles[0], **{field: value})

    with pytest.raises(DomainError) as caught:
        build_signing_plan(replace(request, profiles=(changed, request.profiles[1])))

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
    assert "team or certificate" in caught.value.message


def test_rejects_unauthorized_expected_entitlements() -> None:
    request = valid_request()
    unauthorized = normalize_entitlements(
        {
            "application-identifier": "PREFIX.io.example.app",
            "com.apple.security.application-groups": ["group.io.example.missing"],
        }
    )
    root_expected = ExpectedNodeEntitlements(ROOT.path, unauthorized.values, unauthorized.sha256)

    with pytest.raises(DomainError) as caught:
        build_signing_plan(
            replace(
                request,
                expected_entitlements=(request.expected_entitlements[0], root_expected),
            )
        )

    assert caught.value.code is ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED
    assert dict(caught.value.safe_details)["key"] == "com.apple.security.application-groups"


def test_rejects_unsupported_backend_features() -> None:
    request = valid_request()
    backend = replace(request.backend, features=())

    with pytest.raises(DomainError) as caught:
        build_signing_plan(replace(request, backend=backend))

    assert caught.value.code is ErrorCode.SIGNING_BACKEND_UNSUPPORTED
    assert dict(caught.value.safe_details)["missing_features"] == (
        "per-profile-entitlements",
        "recursive-signing",
    )


def test_rejects_unknown_signable_node_kind() -> None:
    request = valid_request()
    unknown = replace(FRAMEWORK, kind=cast(BundleNodeKind, "unknown-code"))
    graph = replace(request.graph, nodes=(ROOT, EXTENSION, unknown))

    with pytest.raises(DomainError) as caught:
        build_signing_plan(replace(request, graph=graph))

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
    assert dict(caught.value.safe_details)["unsupported_paths"] == (unknown.path.as_posix(),)


def test_rejects_target_identifier_collision() -> None:
    request = valid_request()
    assert request.task.signing is not None
    share = replace(
        request.task.signing.bundles[1],
        target_bundle_id=request.task.bundle_id,
    )
    configured_task = replace(
        request.task,
        signing=replace(
            request.task.signing,
            bundles=(request.task.signing.bundles[0], share),
        ),
    )

    with pytest.raises(DomainError) as caught:
        build_signing_plan(replace(request, task=configured_task))

    assert caught.value.code is ErrorCode.IDENTIFIER_COLLISION
