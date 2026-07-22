"""Contract checks for reviewed signing configuration fixtures."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    EntitlementContext,
    EntitlementMode,
    EntitlementPolicy,
    TaskConfiguration,
    derive_identifier_mappings,
    materialize_entitlements,
    reconcile_bundle_rules,
)

FIXTURE = Path(__file__).parent / "fixtures" / "configuration" / "signing-cases.toml"


def configuration() -> TaskConfiguration:
    return load_configuration(FIXTURE)


def test_fixture_covers_root_only_standard_and_sidestore_variants() -> None:
    tasks = {task.task_name: task for task in configuration().tasks}

    assert tasks["root-only"].signing is None
    standard = tasks["multiple-extensions"].signing
    assert standard is not None
    assert len(standard.bundles) == 4
    assert standard.app_groups == (("shared", "group.io.zeroclover.livecontainer"),)

    sidestore = tasks["sidestore-widget"].signing
    assert sidestore is not None
    assert len(sidestore.bundles) == 5
    assert sidestore.bundles[-1].source_bundle_id == "com.kdt.livecontainer.LiveWidget"
    assert sidestore.bundles[-1].target_bundle_id is not None


def test_non_descendant_fixture_has_working_explicit_mapping() -> None:
    task = next(task for task in configuration().tasks if task.task_name == "non-descendant")
    assert task.signing is not None
    targets = {
        rule.source_bundle_id: rule.target_bundle_id
        for rule in task.signing.bundles
        if rule.target_bundle_id is not None
    }

    mappings = derive_identifier_mappings(
        ["com.upstream.root", "net.unrelated.widget"],
        source_root_bundle_id="com.upstream.root",
        target_root_bundle_id=task.bundle_id,
        explicit_targets=targets,
    )

    assert mappings[-1].target_bundle_id == "io.example.non-descendant.widget"


def test_duplicate_identifier_fixture_produces_aggregated_diagnostic() -> None:
    task = next(task for task in configuration().tasks if task.task_name == "duplicate-source-ids")
    root_path = PurePosixPath("Payload/App.app")
    graph = BundleGraph(
        root_path=root_path,
        nodes=(
            BundleNode(
                path=root_path,
                kind=BundleNodeKind.APP,
                depth=0,
                executable_path=root_path / "App",
                executable_sha256="a" * 64,
                source_bundle_id="com.upstream.duplicate",
            ),
        ),
        source_sha256="b" * 64,
        graph_sha256="c" * 64,
    )

    result = reconcile_bundle_rules(task, graph)

    assert result.diagnostics[0].code == "config.duplicate_bundle_rule"
    assert result.diagnostics[0].details == (("rule_indexes", (0, 1)),)


def test_app_group_remap_and_intentional_drop_fixture_are_executable() -> None:
    task = next(task for task in configuration().tasks if task.task_name == "multiple-extensions")
    assert task.signing is not None
    root_rule = task.signing.bundles[0]
    group = dict(task.signing.app_groups)["shared"]
    context = EntitlementContext(
        team_id="TEAM123456",
        app_identifier_prefix="TEAM123456.",
        source_bundle_id=root_rule.source_bundle_id,
        target_bundle_id=task.bundle_id,
        app_group_rewrites=(("group.com.kdt.livecontainer", group),),
    )

    remapped = materialize_entitlements(
        EntitlementPolicy(EntitlementMode.PRESERVE_SOURCE),
        {"com.apple.security.application-groups": ["group.com.kdt.livecontainer"]},
        context,
    )
    dropped = materialize_entitlements(
        root_rule.entitlement_policy,
        {
            "get-task-allow": True,
            "com.apple.developer.usernotifications.filtering": True,
        },
        context,
        profile_entitlements={"get-task-allow": True},
    )

    assert dict(remapped.values)["com.apple.security.application-groups"] == (group,)
    assert dropped.dropped_keys == ("com.apple.developer.usernotifications.filtering",)
