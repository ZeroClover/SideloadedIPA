"""Side-effect-free reconciliation of bundle inventory and signing rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from sideloadedipa.domain.bundle import BundleGraph, BundleNode
from sideloadedipa.domain.common import Diagnostic, DiagnosticSeverity
from sideloadedipa.domain.config import (
    BundleRule,
    EntitlementMode,
    EntitlementPolicy,
    Task,
)


@dataclass(frozen=True, slots=True)
class ReconciledBundleRule:
    node_path: PurePosixPath
    source_bundle_id: str
    rule: BundleRule


@dataclass(frozen=True, slots=True)
class PolicyReconciliation:
    matches: tuple[ReconciledBundleRule, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics


def _diagnostic(
    code: str,
    message: str,
    task: Task,
    *,
    bundle_id: str | None = None,
    remediation: str | None = None,
    details: tuple[tuple[str, str | int | tuple[int, ...]], ...] = (),
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        message=message,
        task_name=task.task_name,
        bundle_id=bundle_id,
        remediation=remediation,
        details=details,
    )


def _profile_nodes(graph: BundleGraph) -> tuple[BundleNode, ...]:
    return tuple(
        sorted(
            (node for node in graph.nodes if node.profile_bearing), key=lambda node: str(node.path)
        )
    )


def _reconcile_legacy(task: Task, graph: BundleGraph) -> PolicyReconciliation:
    nodes = _profile_nodes(graph)
    diagnostics: list[Diagnostic] = []
    if len(nodes) != 1 or (nodes and nodes[0].path != graph.root_path):
        for node in nodes:
            if node.path == graph.root_path:
                continue
            diagnostics.append(
                _diagnostic(
                    "config.legacy_nested_bundle",
                    "legacy task contains an unconfigured profile-bearing bundle",
                    task,
                    bundle_id=node.source_bundle_id,
                    remediation="add a tasks.signing table with one rule per bundle",
                    details=(("path", str(node.path)),),
                )
            )
    if not nodes or nodes[0].path != graph.root_path:
        diagnostics.append(
            _diagnostic(
                "inventory.root_profile_bundle_missing",
                "inventory does not contain the root profile-bearing bundle",
                task,
                remediation="inspect the selected IPA and its root application",
                details=(("path", str(graph.root_path)),),
            )
        )
    if diagnostics:
        return PolicyReconciliation(matches=(), diagnostics=tuple(diagnostics))

    root = nodes[0]
    if root.source_bundle_id is None:
        return PolicyReconciliation(
            matches=(),
            diagnostics=(
                _diagnostic(
                    "inventory.bundle_identifier_missing",
                    "profile-bearing bundle has no source identifier",
                    task,
                    remediation="repair or select a valid IPA",
                    details=(("path", str(root.path)),),
                ),
            ),
        )
    rule = BundleRule(
        source_bundle_id=root.source_bundle_id,
        target_bundle_id=task.bundle_id,
        role="root",
        entitlement_policy=EntitlementPolicy(mode=EntitlementMode.PROFILE),
    )
    return PolicyReconciliation(
        matches=(ReconciledBundleRule(root.path, root.source_bundle_id, rule),),
        diagnostics=(),
    )


def reconcile_bundle_rules(task: Task, graph: BundleGraph) -> PolicyReconciliation:
    """Match every profile-bearing node to exactly one required signing rule."""

    if task.signing is None:
        return _reconcile_legacy(task, graph)

    rules_by_source: dict[str, list[tuple[int, BundleRule]]] = {}
    for index, rule in enumerate(task.signing.bundles):
        rules_by_source.setdefault(rule.source_bundle_id.casefold(), []).append((index, rule))

    diagnostics: list[Diagnostic] = []
    for entries in rules_by_source.values():
        if len(entries) > 1:
            diagnostics.append(
                _diagnostic(
                    "config.duplicate_bundle_rule",
                    "multiple rules match the same source bundle identifier",
                    task,
                    bundle_id=entries[0][1].source_bundle_id,
                    remediation="keep exactly one rule for this source bundle",
                    details=(("rule_indexes", tuple(index for index, _ in entries)),),
                )
            )

    matched_sources: set[str] = set()
    matches: list[ReconciledBundleRule] = []
    for node in _profile_nodes(graph):
        if node.source_bundle_id is None:
            diagnostics.append(
                _diagnostic(
                    "inventory.bundle_identifier_missing",
                    "profile-bearing bundle has no source identifier",
                    task,
                    remediation="repair or select a valid IPA",
                    details=(("path", str(node.path)),),
                )
            )
            continue
        source_key = node.source_bundle_id.casefold()
        entries = rules_by_source.get(source_key, [])
        if not entries:
            diagnostics.append(
                _diagnostic(
                    "config.unconfigured_bundle",
                    "inventory contains an unconfigured profile-bearing bundle",
                    task,
                    bundle_id=node.source_bundle_id,
                    remediation="add an explicit bundle rule for the selected release asset",
                    details=(("path", str(node.path)),),
                )
            )
            continue
        matched_sources.add(source_key)
        if len(entries) == 1:
            matches.append(ReconciledBundleRule(node.path, node.source_bundle_id, entries[0][1]))

    for source_key, entries in sorted(rules_by_source.items()):
        if source_key not in matched_sources:
            diagnostics.append(
                _diagnostic(
                    "config.absent_bundle_rule",
                    "required bundle rule matches no inventory node",
                    task,
                    bundle_id=entries[0][1].source_bundle_id,
                    remediation="verify the selected release asset or update the stale rule",
                )
            )

    return PolicyReconciliation(matches=tuple(matches), diagnostics=tuple(diagnostics))
