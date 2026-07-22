"""Aggregated configuration and inventory validation before side effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from sideloadedipa.config import EntitlementTemplateContext, load_entitlement_template
from sideloadedipa.domain import (
    BundleGraph,
    Diagnostic,
    EntitlementMode,
    Task,
    derive_identifier_mappings,
    derive_target_bundle_id,
    reconcile_bundle_rules,
)
from sideloadedipa.errors import ConfigurationError, DomainError


@dataclass(frozen=True, slots=True)
class PreflightResult:
    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics


def _add_error(
    diagnostics: list[Diagnostic],
    error: ConfigurationError | DomainError,
    task: Task,
    bundle_id: str | None = None,
) -> None:
    diagnostic = replace(
        error.to_diagnostic(),
        task_name=task.task_name,
        bundle_id=bundle_id or error.bundle_id,
    )
    identity = (diagnostic.code, diagnostic.bundle_id, diagnostic.details)
    if all(
        (existing.code, existing.bundle_id, existing.details) != identity
        for existing in diagnostics
    ):
        diagnostics.append(diagnostic)


def validate_signing_preflight(
    task: Task,
    graph: BundleGraph,
    *,
    repository_root: Path,
    team_id: str,
    app_identifier_prefix: str,
) -> PreflightResult:
    """Collect every currently knowable policy error without performing mutations."""

    reconciliation = reconcile_bundle_rules(task, graph)
    diagnostics = list(reconciliation.diagnostics)
    if task.signing is None:
        return PreflightResult(tuple(diagnostics))

    root = next((node for node in graph.nodes if node.path == graph.root_path), None)
    if root is None or root.source_bundle_id is None:
        return PreflightResult(tuple(diagnostics))
    source_root = root.source_bundle_id

    targets_by_source: dict[str, str] = {}
    for rule in task.signing.bundles:
        try:
            targets_by_source[rule.source_bundle_id] = derive_target_bundle_id(
                rule.source_bundle_id,
                source_root_bundle_id=source_root,
                target_root_bundle_id=task.bundle_id,
                explicit_target_bundle_id=rule.target_bundle_id,
            )
        except DomainError as error:
            _add_error(diagnostics, error, task, rule.source_bundle_id)

    inventory_ids = tuple(
        node.source_bundle_id
        for node in graph.nodes
        if node.profile_bearing and node.source_bundle_id is not None
    )
    explicit_targets = {
        rule.source_bundle_id: rule.target_bundle_id
        for rule in task.signing.bundles
        if rule.target_bundle_id is not None
    }
    try:
        derive_identifier_mappings(
            inventory_ids,
            source_root_bundle_id=source_root,
            target_root_bundle_id=task.bundle_id,
            explicit_targets=explicit_targets,
        )
    except DomainError as error:
        _add_error(diagnostics, error, task)

    app_groups = task.signing.app_groups
    for rule in task.signing.bundles:
        policy = rule.entitlement_policy
        if policy.mode is not EntitlementMode.TEMPLATE or policy.template_path is None:
            continue
        target_bundle_id = targets_by_source.get(rule.source_bundle_id)
        if target_bundle_id is None:
            continue
        try:
            load_entitlement_template(
                repository_root,
                policy.template_path,
                EntitlementTemplateContext(
                    team_id=team_id,
                    app_identifier_prefix=app_identifier_prefix,
                    target_bundle_id=target_bundle_id,
                    app_groups=app_groups,
                ),
            )
        except ConfigurationError as error:
            _add_error(diagnostics, error, task, rule.source_bundle_id)

    return PreflightResult(tuple(diagnostics))


def execute_after_preflight(
    result: PreflightResult,
    *,
    apply_apple_changes: Callable[[], None],
    start_signing: Callable[[], None],
) -> bool:
    """Run mutation and signing only after every preflight diagnostic is clear."""

    if not result.valid:
        return False
    apply_apple_changes()
    start_signing()
    return True
