"""Tests for the aggregated side-effect-free preflight gate."""

from __future__ import annotations

import plistlib
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    BundleRule,
    EntitlementMode,
    EntitlementPolicy,
    SigningPolicy,
    SourceConfig,
    SourceKind,
    Task,
)
from sideloadedipa.signing.preflight import execute_after_preflight, validate_signing_preflight


def bundle(path: str, identifier: str) -> BundleNode:
    bundle_path = PurePosixPath(path)
    return BundleNode(
        path=bundle_path,
        kind=(
            BundleNodeKind.APP
            if bundle_path == PurePosixPath("Payload/App.app")
            else BundleNodeKind.APP_EXTENSION
        ),
        depth=len(bundle_path.parts),
        executable_path=bundle_path / "Executable",
        executable_sha256="a" * 64,
        source_bundle_id=identifier,
    )


def task(*rules: BundleRule) -> Task:
    return Task(
        task_name="Broken",
        app_name="Broken",
        bundle_id="io.example.target",
        source=SourceConfig(SourceKind.DIRECT_URL, "https://example.com/App.ipa"),
        slug="Broken",
        signing=SigningPolicy(
            app_groups=(("shared", "group.io.example.shared"),),
            bundles=rules,
        ),
    )


def graph(*nodes: BundleNode) -> BundleGraph:
    return BundleGraph(
        root_path=PurePosixPath("Payload/App.app"),
        nodes=nodes,
        source_sha256="b" * 64,
        graph_sha256="c" * 64,
    )


def test_aggregates_independent_errors_before_apple_or_signing(
    tmp_path: Path,
) -> None:
    template = tmp_path / "configs" / "signing" / "root.plist"
    template.parent.mkdir(parents=True)
    template.write_bytes(plistlib.dumps({"application-identifier": "${HOME}"}))
    root_rule = BundleRule(
        source_bundle_id="com.example.app",
        target_bundle_id="io.example.shared",
        entitlement_policy=EntitlementPolicy(
            EntitlementMode.TEMPLATE,
            template_path=PurePosixPath("configs/signing/root.plist"),
        ),
    )
    share_rule = BundleRule(
        source_bundle_id="com.example.app.Share",
        target_bundle_id="IO.EXAMPLE.SHARED",
        entitlement_policy=EntitlementPolicy(EntitlementMode.PROFILE),
    )
    removed_rule = BundleRule(
        source_bundle_id="com.example.app.Removed",
        entitlement_policy=EntitlementPolicy(EntitlementMode.PROFILE),
    )
    inventory = graph(
        bundle("Payload/App.app", "com.example.app"),
        bundle("Payload/App.app/PlugIns/Share.appex", "com.example.app.Share"),
        bundle("Payload/App.app/PlugIns/New.appex", "com.example.app.New"),
    )

    result = validate_signing_preflight(
        task(root_rule, share_rule, removed_rule),
        inventory,
        repository_root=tmp_path,
        team_id="TEAM123456",
        app_identifier_prefix="TEAM123456.",
    )
    effects = {"apple": 0, "signing": 0}

    executed = execute_after_preflight(
        result,
        apply_apple_changes=lambda: effects.__setitem__("apple", effects["apple"] + 1),
        start_signing=lambda: effects.__setitem__("signing", effects["signing"] + 1),
    )

    assert executed is False
    assert effects == {"apple": 0, "signing": 0}
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "config.unconfigured_bundle",
        "config.absent_bundle_rule",
        "identifier.collision",
        "entitlements.template_invalid",
    }
    assert all(diagnostic.task_name == "Broken" for diagnostic in result.diagnostics)
    assert all(diagnostic.remediation for diagnostic in result.diagnostics)


def test_valid_preflight_releases_effects_in_order(tmp_path: Path) -> None:
    root_rule = BundleRule(
        source_bundle_id="com.example.app",
        entitlement_policy=EntitlementPolicy(EntitlementMode.PROFILE),
    )
    result = validate_signing_preflight(
        task(root_rule),
        graph(bundle("Payload/App.app", "com.example.app")),
        repository_root=tmp_path,
        team_id="TEAM123456",
        app_identifier_prefix="TEAM123456.",
    )
    effects: list[str] = []

    executed = execute_after_preflight(
        result,
        apply_apple_changes=lambda: effects.append("apple"),
        start_signing=lambda: effects.append("signing"),
    )

    assert result.valid is True
    assert executed is True
    assert effects == ["apple", "signing"]
