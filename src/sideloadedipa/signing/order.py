"""Validated deterministic deepest-first ordering for signable bundle graphs."""

from __future__ import annotations

from pathlib import PurePosixPath

from sideloadedipa.domain import BundleGraph, BundleNode, BundleNodeKind
from sideloadedipa.errors import DomainError, ErrorCode


def _invalid(message: str, *paths: PurePosixPath) -> DomainError:
    return DomainError(
        ErrorCode.SIGNING_PLAN_INVALID,
        message,
        remediation="re-inspect the source IPA before building a signing plan",
        safe_details=(("paths", tuple(sorted(path.as_posix() for path in paths))),),
    )


def signing_order(graph: BundleGraph) -> tuple[BundleNode, ...]:
    """Return child-before-parent order with deterministic sibling traversal."""

    paths = tuple(node.path for node in graph.nodes)
    if len(set(paths)) != len(paths):
        duplicates = tuple(path for path in set(paths) if paths.count(path) > 1)
        raise _invalid("bundle graph contains duplicate signable paths", *duplicates)
    by_path = {node.path: node for node in graph.nodes}
    root = by_path.get(graph.root_path)
    if root is None or root.kind is not BundleNodeKind.APP or root.parent_path is not None:
        raise _invalid("bundle graph has no valid root application", graph.root_path)

    children: dict[PurePosixPath, list[BundleNode]] = {path: [] for path in by_path}
    for node in graph.nodes:
        if node.path == graph.root_path:
            continue
        parent_path = node.parent_path
        if parent_path is None or parent_path not in by_path:
            raise _invalid("signable node references a missing parent", node.path)
        if not node.path.is_relative_to(parent_path) or node.path == parent_path:
            raise _invalid(
                "signable node path is not contained by its parent", node.path, parent_path
            )
        children[parent_path].append(node)

    computed_depth: dict[PurePosixPath, int] = {}
    ordered: list[BundleNode] = []

    def visit(node: BundleNode, depth: int) -> None:
        computed_depth[node.path] = depth
        for child in sorted(children[node.path], key=lambda value: value.path.as_posix()):
            visit(child, depth + 1)
        ordered.append(node)

    visit(root, 0)
    wrong_depth = tuple(
        node.path for node in graph.nodes if node.depth != computed_depth[node.path]
    )
    if wrong_depth:
        raise _invalid("bundle graph signing depth disagrees with parent edges", *wrong_depth)
    return tuple(ordered)
