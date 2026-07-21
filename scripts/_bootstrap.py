"""Load packaged legacy modules directly from a repository checkout."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


def load_legacy(module_name: str) -> ModuleType:
    source_root = Path(__file__).resolve().parents[1] / "src"
    source_value = str(source_root)
    if source_value not in sys.path:
        sys.path.insert(0, source_value)
    return importlib.import_module(f"sideloadedipa.legacy.{module_name}")
