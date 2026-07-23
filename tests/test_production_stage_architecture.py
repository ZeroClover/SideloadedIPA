"""Architecture checks for the concrete production-stage extraction."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

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
