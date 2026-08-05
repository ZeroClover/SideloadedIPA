"""Golden-value digest contracts for the canonical-serialization refactor.

These tests pin the durable signing-cache and verification digest contracts
*before* the shared-serializer unification in
``openspec/changes/consolidate-shared-primitives-and-test-fidelity``. Any
byte-level change to canonical JSON serialization must keep every golden
value below unchanged.

The corpus covers every serializer touched by the unification:

- ``domain.entitlements.normalize_entitlements`` (policy digests)
- ``verification.entitlements`` comparison evidence digests
- ``pipeline.stages.signing_cache.policy_sha256`` (signing-cache policy digest)
- ``pipeline.stage_manifests.stage_manifest_sha256``
- ``pipeline.run_reports.canonical_run_report_json`` report digest
- ``signing.planner`` signing-plan digests
- ``adapters.apple.state`` snapshot digests
- ``verification.integrity`` structure evidence digests
- ``signing.profile_storage`` profile-manifest digests
- ``ipa.graph`` bundle-graph digests
- ``apple.commands`` profile device-set digests
- ``util.atomics.canonical_json`` (NaN handling characterization)

There are no standalone entitlement documents under ``tests/fixtures/``; the
corpus therefore covers the production entitlement template under
``configs/signing/`` rendered through the production loader, plus
representative inline documents exercising every JSON value shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from sideloadedipa.adapters.apple import normalized_apple_state
from sideloadedipa.apple.commands import _profile_device_set_sha256
from sideloadedipa.cache.decisions import RebuildDecision, RebuildReason
from sideloadedipa.config import (
    EntitlementTemplateContext,
    load_configuration,
    load_entitlement_template,
)
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleCapabilityState,
    AppleCertificateState,
    AppleDeviceState,
    AppleProfileState,
    BundleNode,
    BundleNodeKind,
    PipelineStage,
    ProfileManifestEntry,
    PublicationResult,
    SourceAsset,
    StageStatus,
    normalize_entitlements,
)
from sideloadedipa.ipa.graph import _graph_digest
from sideloadedipa.pipeline.run_reports import (
    RunReport,
    TaskRunEvidence,
    canonical_run_report_json,
)
from sideloadedipa.pipeline.stage_manifests import (
    finish_stage,
    stage_manifest_sha256,
    start_stage,
)
from sideloadedipa.pipeline.stages.signing_cache import policy_sha256
from sideloadedipa.signing.planner import _plan_sha256
from sideloadedipa.signing.profile_storage import (
    build_profile_manifest,
    profile_relative_path,
)
from sideloadedipa.util.atomics import canonical_json
from sideloadedipa.verification.entitlements import compare_entitlements
from sideloadedipa.verification.integrity import _canonical_sha256, _structure_document
from tests.conftest import publication_candidate

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)

_REPRESENTATIVE_ENTITLEMENT_DIGESTS = {
    "preserve-source-livecontainer": (
        {
            "application-identifier": "SOURCETEAM.com.kdt.livecontainer",
            "com.apple.developer.team-identifier": "SOURCETEAM",
            "keychain-access-groups": [
                "SOURCETEAM.com.kdt.livecontainer",
                "unrelated.shared.group",
            ],
            "com.apple.security.application-groups": ["group.com.kdt.shared"],
            "com.apple.developer.healthkit": True,
        },
        "df6587e59a47d2730af03a4da7eec592b0d0ad2c32c02272e6be938901b1756c",
    ),
    "minimal-development": (
        {
            "application-identifier": "TEAMID.io.example.app",
            "com.apple.developer.team-identifier": "TEAMID",
            "get-task-allow": True,
        },
        "16ac1f22ed05d773413860606e4783afa86a21ba449a389df5bd9f065c7a7616",
    ),
    "nested-mixed-types": (
        {
            "a.nested": {"inner": [1, 2.5, "three", False, None]},
            "z-empty": [],
            "m-empty-map": {},
        },
        "33eebfe3c26b35d7eb9fc5f970556e1853bd8a7f35354530e504eaaaea7f5466",
    ),
}

_POLICY_DIGESTS = {
    "MyApp": "d799c6a5e3d3377e841db4a09704a27fc60a81c17d4bad0b425ebe8256500c26",
    "AnotherApp": "a2d1195102a6ed47e02db8da9b2598693ef7eda3d43a724a789b1cb439a8361f",
    "SpecialApp": "fc5001521f3c863a3bc97e82cad4c801dc4855c24c537cb62e8f1d988a5b8acd",
    "LiveContainer": "1dc7d2fffb3a46f8466d192b1fbe0f62c9d8ab52e1410777b005cf0fb4f9c66b",
}


def _representative_bundle_nodes() -> tuple[BundleNode, ...]:
    entitlements = normalize_entitlements({"application-identifier": "TEAMID.io.example.app"})
    root = PurePosixPath("Payload/App.app")
    extension = root / "PlugIns/Share.appex"
    return (
        BundleNode(
            path=root,
            kind=BundleNodeKind.APP,
            depth=0,
            executable_path=root / "App",
            executable_sha256="1" * 64,
            source_bundle_id="com.upstream.app",
            info_plist_sha256="2" * 64,
            version="42",
            short_version="1.2.3",
            embedded_profile_sha256="3" * 64,
            xml_entitlements_sha256=entitlements.sha256,
            entitlements=entitlements.values,
        ),
        BundleNode(
            path=extension,
            kind=BundleNodeKind.APP_EXTENSION,
            depth=1,
            executable_path=extension / "Share",
            executable_sha256="4" * 64,
            parent_path=root,
            source_bundle_id="com.upstream.app.Share",
            info_plist_sha256="5" * 64,
            version="42",
            short_version="1.2.3",
            embedded_profile_sha256="6" * 64,
            xml_entitlements_sha256=entitlements.sha256,
            entitlements=entitlements.values,
        ),
    )


def test_normalize_entitlements_digests_are_stable_for_representative_documents() -> None:
    for name, (document, expected_sha256) in _REPRESENTATIVE_ENTITLEMENT_DIGESTS.items():
        assert normalize_entitlements(document).sha256 == expected_sha256, name


def test_normalize_entitlements_digest_is_stable_for_the_production_template() -> None:
    rendered = load_entitlement_template(
        Path("."),
        Path("configs/signing/livecontainer/root-process.plist"),
        EntitlementTemplateContext(
            team_id="TEAMID",
            app_identifier_prefix="TEAMID.",
            target_bundle_id="io.example.app.livecontainer",
            app_groups=(("shared", "group.io.example.shared"),),
        ),
    )

    assert normalize_entitlements(rendered).sha256 == (
        "f0fa2c7d47205926f4b03e49747169b0bb897b7de47b7f1b085e05f7905ff6b9"
    )


def test_policy_digests_are_stable_for_canonical_configuration_fixtures() -> None:
    configuration = load_configuration(Path("configs/tasks.toml.example"))

    assert {task.task_name: policy_sha256(task) for task in configuration.tasks} == (
        _POLICY_DIGESTS
    )


def test_signing_plan_digest_is_stable(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    plan = publication_candidate(artifact).plan

    assert _plan_sha256(plan) == (
        "d98f43199f30e4e6fc25c4a6d9243f7d757ed77dd380f6169a97f3a04ebe4e13"
    )


def test_apple_snapshot_digest_is_stable() -> None:
    snapshot = normalized_apple_state(
        bundle_ids=(
            AppleBundleIdentifierState(
                "BUNDLE_ONE",
                "io.example.app",
                "Example",
                "IOS",
                "TEAMID",
            ),
        ),
        capabilities=(AppleCapabilityState("CAPABILITY_ONE", "BUNDLE_ONE", "APP_GROUPS"),),
        certificates=(
            AppleCertificateState(
                "CERTIFICATE_ONE",
                "Development",
                "DEVELOPMENT",
                "Apple Development",
                "1234ABCD",
                "IOS",
                "2027-10-22T00:00:00+00:00",
                "7" * 64,
            ),
        ),
        devices=(
            AppleDeviceState(
                "DEVICE_ONE",
                "Test iPhone",
                "IOS",
                "ENABLED",
                "IPHONE",
                "8" * 64,
            ),
        ),
        profiles=(
            AppleProfileState(
                "PROFILE_ONE",
                "Example Development",
                "IOS",
                "IOS_APP_DEVELOPMENT",
                "ACTIVE",
                "UUID-ONE",
                "2026-07-21T00:00:00+00:00",
                "2027-07-21T00:00:00+00:00",
                "9" * 64,
                "BUNDLE_ONE",
                ("CERTIFICATE_ONE",),
                ("DEVICE_ONE",),
            ),
        ),
    )

    assert snapshot.snapshot_sha256 == (
        "0ef213a5abdc713de06c01e27f255b715ad60b7fc888c5f1d57d0bf15a046a73"
    )


def test_integrity_structure_digest_is_stable() -> None:
    assert _canonical_sha256(_structure_document(_representative_bundle_nodes())) == (
        "092eae1d23e385b8051c929c157669e75bda07b8fbde40eed3fa5e09603104cf"
    )


def test_profile_manifest_digest_is_stable() -> None:
    task_name = "Example"
    manifest = build_profile_manifest(
        task_name=task_name,
        snapshot_sha256="a" * 64,
        entries=(
            ProfileManifestEntry(
                target_bundle_id="io.example.app",
                bundle_resource_id="BUNDLE_ONE",
                profile_resource_id="PROFILE_ONE",
                certificate_resource_id="CERTIFICATE_ONE",
                profile_path=profile_relative_path(task_name, "io.example.app"),
                profile_sha256="b" * 64,
                device_set_sha256="c" * 64,
                expires_at=NOW + timedelta(days=90),
            ),
        ),
    )

    assert manifest.manifest_sha256 == (
        "6b8554a47110c9cf691697867bbade71e24e41efdc2d60173f3600590ee1c25d"
    )


def test_bundle_graph_digest_is_stable() -> None:
    nodes = _representative_bundle_nodes()

    assert _graph_digest(nodes[0].path, list(nodes), "d" * 64) == (
        "de1e34b8e4b45f65143de3d62191df6fc7ee7031c0261b7b01630726f956268e"
    )


def test_profile_device_set_digest_is_stable() -> None:
    assert _profile_device_set_sha256(("DEVICE_TWO", "DEVICE_ONE")) == (
        "90ae4d25d4ba9b68e6ed7c122d8dad82a318acaa77dfae5d1935d1a7523b6d47"
    )


def test_stage_manifest_digests_are_stable() -> None:
    started = start_stage(
        task_name="Example",
        stage=PipelineStage.SOURCE,
        started_at=NOW,
        input_sha256="a" * 64,
    )
    source = finish_stage(
        started,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=1),
        result_sha256="b" * 64,
    )
    assert stage_manifest_sha256(source) == (
        "04250a3717fd250a74ac9cce9084148c22914a872962c6fe989ac586db28a12c"
    )

    linked = start_stage(
        task_name="Example",
        stage=PipelineStage.INVENTORY,
        started_at=NOW + timedelta(seconds=2),
        input_sha256="b" * 64,
        predecessor=source,
    )
    linked = finish_stage(
        linked,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=3),
        result_sha256="c" * 64,
    )
    assert stage_manifest_sha256(linked) == (
        "a1ea64022462ee0a66ed9b6d165edca29577edc5e2cb5fce5bb2a8f4c4b1613f"
    )


def test_run_report_digest_is_stable(tmp_path: Path) -> None:
    artifact = tmp_path / "Example.ipa"
    artifact.write_bytes(b"verified")
    candidate = publication_candidate(artifact)
    running = start_stage(
        task_name="Example",
        stage=PipelineStage.SOURCE,
        started_at=NOW,
        input_sha256=None,
    )
    stage = finish_stage(
        running,
        status=StageStatus.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=2),
        result_sha256="5" * 64,
    )
    evidence = TaskRunEvidence(
        task_name="Example",
        stages=(stage,),
        source=SourceAsset(
            "asset-1",
            "Example.ipa",
            "https://example.invalid/download",
            "1.2.3",
            NOW - timedelta(days=1),
            PurePosixPath("private/Example.ipa"),
            "0" * 64,
        ),
        graph_sha256=candidate.plan.graph_sha256,
        plan=candidate.plan,
        capability_classifications=(("io.example.app", "APP_GROUPS", "api-additive"),),
        apple_resource_ids=(("bundle-id", "RESOURCE-1"),),
        cache_decision=RebuildDecision(
            "Example", True, RebuildReason.FINGERPRINT_CHANGED, "6" * 64, "7" * 64
        ),
        verification=candidate.verification,
        publication=PublicationResult(
            "Example",
            "apps/example/1.2.3/hash-Example.ipa",
            "https://cdn.example/apps/example/1.2.3/hash-Example.ipa",
            candidate.artifact_sha256,
            "site/apps.json",
            "8" * 64,
            ("apps/example/1.0/Example.ipa",),
        ),
    )

    payload = canonical_run_report_json(
        RunReport("run-123", NOW, NOW + timedelta(seconds=3), (evidence,))
    )

    assert json.loads(payload)["report_sha256"] == (
        "5bf4ed098ca5501b073568061d2b601372b175a129b6f2f49e016a2d8f56ade4"
    )


def test_verification_evidence_digests_are_stable() -> None:
    comparison = compare_entitlements(
        {"keychain-access-groups": ["A", "B"]},
        {"keychain-access-groups": ["B"]},
    )

    difference = comparison.differences[0]
    assert (difference.path, difference.reason) == ("keychain-access-groups", "set-mismatch")
    assert difference.expected_sha256 == (
        "b64e3448a83a5b86466465080361c1a7e1157a27ddccd4b68069cb18caffb74a"
    )
    assert difference.actual_sha256 == (
        "a1c69ab8192836489304dbe2e21027d59850019ee150fdefba58d5b9bb3a14a3"
    )


def test_fixture_corpus_contains_no_non_finite_numbers() -> None:
    """The canonical_json NaN gap is unexploited by every durable document."""

    documents: list[object] = [
        *[document for document, _ in _REPRESENTATIVE_ENTITLEMENT_DIGESTS.values()],
        json.loads(Path("tests/fixtures/asc/apple-state.json").read_text()),
        json.loads(Path("tests/fixtures/inventory/livecontainer-standard.json").read_text()),
        json.loads(Path("tests/fixtures/inventory/livecontainer-sidestore.json").read_text()),
    ]
    for document in documents:
        # Raises ValueError if any non-finite number were present; the golden
        # digests above prove the corpus must keep serializing identically.
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
        canonical_json(document)
