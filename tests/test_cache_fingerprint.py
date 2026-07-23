"""Tests for complete signing cache fingerprints."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from sideloadedipa.cache.fingerprint import (
    SigningCacheFingerprint,
    ToolFingerprint,
    build_signing_cache_fingerprint,
)
from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    ProfileManifestEntry,
    ProfileResourceManifest,
    ProfileType,
    ProvisioningProfile,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    SourceAsset,
    normalize_entitlements,
    thaw_json,
)

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
ROOT = PurePosixPath("Payload/App.app")


@dataclass(frozen=True)
class FixtureInputs:
    source: SourceAsset
    policy_sha256: str
    graph: BundleGraph
    entitlement_template_sha256: tuple[tuple[str, str], ...]
    resource_manifest: ProfileResourceManifest
    profiles: tuple[ProvisioningProfile, ...]
    plan: SigningPlan
    device_set_sha256: str
    tools: tuple[ToolFingerprint, ...]


def inputs() -> FixtureInputs:
    entitlements = normalize_entitlements(
        {"application-identifier": "TEAM.io.example.app", "get-task-allow": True}
    )
    profile = ProvisioningProfile(
        "PROFILE",
        "Example Dev",
        ProfileType.IOS_APP_DEVELOPMENT,
        "io.example.app",
        "TEAM.io.example.app",
        "TEAM",
        "c" * 64,
        ("device-b", "device-a"),
        NOW,
        NOW + timedelta(days=90),
        "d" * 64,
        PurePosixPath("Example/profile.mobileprovision"),
        entitlements.values,
    )
    manifest_entry = ProfileManifestEntry(
        "io.example.app",
        "BUNDLE",
        profile.resource_id,
        "CERTIFICATE",
        profile.path,
        profile.profile_sha256,
        "e" * 64,
        profile.expires_at,
    )
    manifest = ProfileResourceManifest(1, "Example", "f" * 64, (manifest_entry,), "1" * 64)
    backend = SigningBackendIdentity("zsign", "1.1.1", "2" * 64, "1")
    plan = SigningPlan(
        "Example",
        "3" * 64,
        "4" * 64,
        "c" * 64,
        backend,
        (
            SigningNodePlan(
                ROOT,
                ROOT / "App",
                BundleNodeKind.APP,
                0,
                "io.example.app",
                profile.resource_id,
                profile.path,
                profile.profile_sha256,
                entitlements.values,
                entitlements.sha256,
            ),
        ),
        "5" * 64,
    )
    graph = BundleGraph(
        ROOT,
        (
            BundleNode(
                ROOT,
                BundleNodeKind.APP,
                0,
                ROOT / "App",
                "6" * 64,
                source_bundle_id="com.upstream.app",
            ),
        ),
        plan.source_ipa_sha256,
        plan.graph_sha256,
    )
    source = SourceAsset(
        "ASSET",
        "App.ipa",
        "https://download.example/App.ipa?token=private",
        "v1",
        NOW,
        PurePosixPath("App.ipa"),
        plan.source_ipa_sha256,
    )
    return FixtureInputs(
        source,
        "7" * 64,
        graph,
        (("configs/app.plist", "8" * 64),),
        manifest,
        (profile,),
        plan,
        "9" * 64,
        (ToolFingerprint("asc", "3.1.1", "a" * 64),),
    )


def build(values: FixtureInputs) -> SigningCacheFingerprint:
    return build_signing_cache_fingerprint(
        source=values.source,
        policy_sha256=values.policy_sha256,
        graph=values.graph,
        entitlement_template_sha256=values.entitlement_template_sha256,
        resource_manifest=values.resource_manifest,
        profiles=values.profiles,
        plan=values.plan,
        device_set_sha256=values.device_set_sha256,
        tools=values.tools,
    )


def test_every_cache_input_category_changes_the_fingerprint() -> None:
    original = inputs()
    baseline = build(original).sha256
    mutations: list[FixtureInputs] = []

    mutations.append(replace(original, source=replace(original.source, asset_id="OTHER")))
    mutations.append(
        replace(
            original,
            source=replace(original.source, source_url="https://download.example/Other.ipa"),
        )
    )
    mutations.append(replace(original, source=replace(original.source, sha256="0" * 64)))
    mutations.append(replace(original, policy_sha256="0" * 64))
    mutations.append(replace(original, graph=replace(original.graph, graph_sha256="0" * 64)))
    mutations.append(
        replace(original, entitlement_template_sha256=(("configs/app.plist", "0" * 64),))
    )
    mutations.append(
        replace(
            original,
            resource_manifest=replace(original.resource_manifest, manifest_sha256="0" * 64),
        )
    )
    profile = original.profiles[0]
    mutations.append(
        replace(
            original,
            profiles=(replace(profile, expires_at=profile.expires_at + timedelta(days=1)),),
        )
    )
    mutations.append(replace(original, plan=replace(original.plan, certificate_sha256="0" * 64)))
    mutations.append(replace(original, device_set_sha256="0" * 64))
    mutations.append(replace(original, tools=(ToolFingerprint("asc", "3.2.0", "a" * 64),)))

    assert len({build(value).sha256 for value in mutations}) == len(mutations)
    assert all(build(value).sha256 != baseline for value in mutations)


def test_direct_source_fingerprint_retains_digest_and_redacted_url_identity() -> None:
    values = inputs()
    fingerprint = build(values)
    components = dict(fingerprint.components)
    source = thaw_json(components["source"])
    assert isinstance(source, dict)

    assert source["sha256"] == values.source.sha256
    assert source["source_url_sha256"] == (
        "28bf531defc4f99dce864f4de75da8210866659a1e5da87bd92ec32fc0038f56"
    )
    assert "private" not in repr(fingerprint.components)


def test_unrelated_task_fingerprint_remains_stable() -> None:
    first = inputs()
    unrelated = inputs()
    unrelated = replace(unrelated, plan=replace(unrelated.plan, task_name="Other"))
    before = build(unrelated)

    first = replace(first, policy_sha256="0" * 64)

    assert build(first).sha256 != build(inputs()).sha256
    assert build(unrelated) == before
