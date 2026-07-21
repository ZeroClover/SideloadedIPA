"""Tests for immutable domain model boundaries."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    EntitlementMode,
    EntitlementPolicy,
    IdentifierStrategy,
    ProfileType,
    SigningPolicy,
    SourceConfig,
    SourceKind,
    Task,
    UnknownProfileBundlePolicy,
    apple,
    bundle,
    common,
    config,
    pipeline,
    signing,
)


@pytest.mark.parametrize("module", [apple, bundle, common, config, pipeline, signing])
def test_every_domain_dataclass_is_frozen(module: object) -> None:
    classes = [
        value
        for _, value in inspect.getmembers(module, inspect.isclass)
        if value.__module__ == module.__name__ and is_dataclass(value)
    ]

    assert classes
    assert all(value.__dataclass_params__.frozen for value in classes)


def test_task_and_bundle_graph_are_immutable() -> None:
    task = Task(
        task_name="LiveContainer",
        app_name="LiveContainer",
        bundle_id="io.zeroclover.app.livecontainer",
        source=SourceConfig(
            kind=SourceKind.GITHUB_RELEASE,
            location="https://github.com/LiveContainer/LiveContainer",
            release_glob="LiveContainer.ipa",
        ),
        slug="LiveContainer",
        signing=SigningPolicy(
            id_strategy=IdentifierStrategy.PRESERVE_SOURCE_SUFFIX,
            unknown_profile_bundles=UnknownProfileBundlePolicy.ERROR,
            profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        ),
    )
    node = BundleNode(
        path=PurePosixPath("Payload/LiveContainer.app"),
        kind=BundleNodeKind.APP,
        depth=0,
        executable_path=PurePosixPath("Payload/LiveContainer.app/LiveContainer"),
        executable_sha256="a" * 64,
        source_bundle_id="com.kdt.livecontainer",
    )
    graph = BundleGraph(
        root_path=node.path,
        nodes=(node,),
        source_sha256="b" * 64,
        graph_sha256="c" * 64,
    )

    assert task.signing is not None
    assert graph.nodes[0].profile_bearing is True
    with pytest.raises(FrozenInstanceError):
        task.slug = "changed"  # type: ignore[misc]


def test_entitlement_policy_uses_explicit_mode() -> None:
    policy = EntitlementPolicy(mode=EntitlementMode.PROFILE)

    assert policy.template_path is None
    assert policy.allowed_drops == ()
