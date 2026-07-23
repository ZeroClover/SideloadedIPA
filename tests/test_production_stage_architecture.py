"""Architecture checks for the concrete production-stage extraction."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import sideloadedipa.pipeline.stages.models as stage_models
from sideloadedipa.pipeline.stages.models import PreparedContext

STAGE_ROOT = Path("src/sideloadedipa/pipeline/stages")
STAGE_PREFIX = "sideloadedipa.pipeline.stages."


def stage_modules() -> dict[str, Path]:
    return {
        f"{STAGE_PREFIX}{path.stem}": path
        for path in STAGE_ROOT.glob("*.py")
        if path.name != "__init__.py"
    }


def stage_edges(modules: dict[str, Path]) -> dict[str, set[str]]:
    edges = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in modules:
                edges[name].add(node.module)
            elif isinstance(node, ast.Import):
                edges[name].update(alias.name for alias in node.names if alias.name in modules)
    return edges


def test_concrete_stage_import_graph_is_acyclic_and_leaf_owned() -> None:
    modules = stage_modules()
    edges = stage_edges(modules)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"stage import cycle reaches {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in edges[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in modules:
        visit(module)
        importlib.import_module(module)
        source = modules[module].read_text()
        assert "sideloadedipa.pipeline.production" not in source
        assert "from sideloadedipa.domain import" not in source


def test_production_coordinator_remains_thin_and_signing_execution_is_single_path() -> None:
    production = Path("src/sideloadedipa/pipeline/production.py").read_text()
    signing = (STAGE_ROOT / "signing.py").read_text()

    assert len(production.splitlines()) < 500
    assert signing.count("execute_package_signing(") == 1
    assert "class ProductionStage" not in production
    assert "ServiceContainer" not in production
    run_source = production.split("    def run(", maxsplit=1)[1]
    assert run_source.count("with self._prepared(") == 1
    assert "self.plan(" not in run_source
    assert "self.sign(" not in run_source
    assert "self.verify(" not in run_source
    assert "self.publish(" not in run_source


def test_prepared_context_memoizes_one_signing_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    plan = object()

    def build(_request):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return plan

    monkeypatch.setattr(stage_models, "plan_package_signing", build)
    prepared = PreparedContext(object(), object(), object())  # type: ignore[arg-type]

    assert prepared.plan is plan
    assert prepared.plan is plan
    assert calls == 1
