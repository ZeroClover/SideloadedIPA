"""Tests for exact inventory-to-signing-rule reconciliation."""

from __future__ import annotations

from pathlib import PurePosixPath

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
    reconcile_bundle_rules,
)


def node(path: str, kind: BundleNodeKind, bundle_id: str | None) -> BundleNode:
    bundle_path = PurePosixPath(path)
    return BundleNode(
        path=bundle_path,
        kind=kind,
        depth=len(bundle_path.parts),
        executable_path=bundle_path / "Executable",
        executable_sha256="a" * 64,
        source_bundle_id=bundle_id,
    )


def graph(*nodes: BundleNode) -> BundleGraph:
    return BundleGraph(
        root_path=PurePosixPath("Payload/App.app"),
        nodes=nodes,
        source_sha256="b" * 64,
        graph_sha256="c" * 64,
    )


def rule(source_bundle_id: str) -> BundleRule:
    return BundleRule(
        source_bundle_id=source_bundle_id,
        entitlement_policy=EntitlementPolicy(EntitlementMode.PROFILE),
    )


def task(*rules: BundleRule, legacy: bool = False) -> Task:
    return Task(
        task_name="App",
        app_name="App",
        bundle_id="io.example.app",
        source=SourceConfig(SourceKind.DIRECT_URL, "https://example.com/App.ipa"),
        slug="App",
        signing=(
            None
            if legacy
            else SigningPolicy(
                bundles=rules,
            )
        ),
    )


def test_matches_every_profile_bundle_and_ignores_frameworks() -> None:
    root = node("Payload/App.app", BundleNodeKind.APP, "com.example.app")
    extension = node(
        "Payload/App.app/PlugIns/Share.appex",
        BundleNodeKind.APP_EXTENSION,
        "com.example.app.Share",
    )
    framework = node(
        "Payload/App.app/Frameworks/Kit.framework",
        BundleNodeKind.FRAMEWORK,
        None,
    )

    result = reconcile_bundle_rules(
        task(rule("com.example.app.Share"), rule("com.example.app")),
        graph(root, extension, framework),
    )

    assert result.valid is True
    assert [match.source_bundle_id for match in result.matches] == [
        "com.example.app",
        "com.example.app.Share",
    ]


def test_aggregates_duplicate_absent_and_unconfigured_diagnostics() -> None:
    root = node("Payload/App.app", BundleNodeKind.APP, "com.example.app")
    new_extension = node(
        "Payload/App.app/PlugIns/New.appex",
        BundleNodeKind.APP_EXTENSION,
        "com.example.app.New",
    )

    result = reconcile_bundle_rules(
        task(
            rule("com.example.app"),
            rule("COM.EXAMPLE.APP"),
            rule("com.example.app.Removed"),
        ),
        graph(root, new_extension),
    )

    assert result.valid is False
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "config.duplicate_bundle_rule",
        "config.unconfigured_bundle",
        "config.absent_bundle_rule",
    ]
    assert result.diagnostics[0].details == (("rule_indexes", (0, 1)),)
    assert result.diagnostics[1].details == (("path", "Payload/App.app/PlugIns/New.appex"),)
    assert result.diagnostics[2].bundle_id == "com.example.app.Removed"


def test_legacy_root_only_task_gets_compatible_profile_rule() -> None:
    root = node("Payload/App.app", BundleNodeKind.APP, "com.upstream.app")

    result = reconcile_bundle_rules(task(legacy=True), graph(root))

    assert result.valid is True
    assert result.matches[0].rule.target_bundle_id == "io.example.app"
    assert result.matches[0].rule.entitlement_policy.mode is EntitlementMode.PROFILE


def test_legacy_task_reports_all_nested_profile_bundles() -> None:
    root = node("Payload/App.app", BundleNodeKind.APP, "com.example.app")
    first = node(
        "Payload/App.app/PlugIns/First.appex",
        BundleNodeKind.APP_EXTENSION,
        "com.example.app.First",
    )
    second = node(
        "Payload/App.app/PlugIns/Second.appex",
        BundleNodeKind.APP_EXTENSION,
        "com.example.app.Second",
    )

    result = reconcile_bundle_rules(task(legacy=True), graph(root, first, second))

    assert [diagnostic.bundle_id for diagnostic in result.diagnostics] == [
        "com.example.app.First",
        "com.example.app.Second",
    ]
    assert result.matches == ()


def test_reports_missing_source_identifier_for_typed_and_legacy_policy() -> None:
    unidentified = node("Payload/App.app", BundleNodeKind.APP, None)

    typed = reconcile_bundle_rules(task(), graph(unidentified))
    legacy = reconcile_bundle_rules(task(legacy=True), graph(unidentified))

    assert typed.diagnostics[0].code == "inventory.bundle_identifier_missing"
    assert legacy.diagnostics[0].code == "inventory.bundle_identifier_missing"


def test_reports_missing_root_profile_bundle_for_legacy_task() -> None:
    framework = node(
        "Payload/App.app/Frameworks/Kit.framework",
        BundleNodeKind.FRAMEWORK,
        None,
    )

    result = reconcile_bundle_rules(task(legacy=True), graph(framework))

    assert result.diagnostics[0].code == "inventory.root_profile_bundle_missing"
