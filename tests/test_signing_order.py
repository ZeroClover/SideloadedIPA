"""Tests for validated deepest-first signing order."""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath

import pytest

from sideloadedipa.domain import BundleGraph, BundleNode, BundleNodeKind
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.signing.order import signing_order


def node(
    path: str,
    kind: BundleNodeKind,
    depth: int,
    parent: str | None,
) -> BundleNode:
    value = PurePosixPath(path)
    return BundleNode(
        path=value,
        kind=kind,
        depth=depth,
        executable_path=value / "Executable",
        executable_sha256="a" * 64,
        parent_path=PurePosixPath(parent) if parent is not None else None,
    )


ROOT = node("Payload/App.app", BundleNodeKind.APP, 0, None)
SHARE = node("Payload/App.app/PlugIns/Share.appex", BundleNodeKind.APP_EXTENSION, 1, str(ROOT.path))
SHARE_FRAMEWORK = node(
    "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.framework",
    BundleNodeKind.FRAMEWORK,
    2,
    str(SHARE.path),
)
SHARE_DYLIB = node(
    "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.framework/Support.dylib",
    BundleNodeKind.DYLIB,
    3,
    str(SHARE_FRAMEWORK.path),
)
NESTED_APP = node("Payload/App.app/Watch/Nested.app", BundleNodeKind.APP, 1, str(ROOT.path))
NESTED_EXECUTABLE = node(
    "Payload/App.app/Watch/Nested.app/Helpers/Runner",
    BundleNodeKind.EXECUTABLE,
    2,
    str(NESTED_APP.path),
)


def graph(*nodes: BundleNode) -> BundleGraph:
    return BundleGraph(ROOT.path, nodes, "b" * 64, "c" * 64)


def test_orders_each_subtree_deepest_first_with_root_last() -> None:
    nodes = (ROOT, NESTED_EXECUTABLE, SHARE, SHARE_DYLIB, NESTED_APP, SHARE_FRAMEWORK)

    result = signing_order(graph(*nodes))
    repeated = signing_order(graph(*reversed(nodes)))

    expected = (
        SHARE_DYLIB,
        SHARE_FRAMEWORK,
        SHARE,
        NESTED_EXECUTABLE,
        NESTED_APP,
        ROOT,
    )
    assert result == expected
    assert repeated == expected
    positions = {value.path: index for index, value in enumerate(result)}
    assert all(
        node.parent_path is None or positions[node.path] < positions[node.parent_path]
        for node in result
    )
    assert result[-1] is ROOT


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ((SHARE,), "root application"),
        (
            (ROOT, replace(SHARE, parent_path=PurePosixPath("Payload/Missing.app"))),
            "missing parent",
        ),
        ((ROOT, replace(SHARE, depth=3)), "depth"),
        ((ROOT, ROOT), "duplicate"),
    ],
)
def test_rejects_invalid_graph_relationships(nodes: tuple[BundleNode, ...], message: str) -> None:
    with pytest.raises(DomainError, match=message) as caught:
        signing_order(graph(*nodes))

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
