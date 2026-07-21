"""Compatibility checks for original script module names and entry points."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

LEGACY_MODULES = (
    "app_icon",
    "build_backend_qualification_fixture",
    "check_changes",
    "compare_backend_qualification",
    "exercise_codesign_oracle",
    "exercise_zsign_backend",
    "qualify_backend_prerequisites",
    "r2_store",
    "reconcile_icons",
    "sync_profiles_asc",
)


def test_original_module_names_alias_packaged_implementations() -> None:
    scripts = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        modules = [importlib.import_module(name) for name in LEGACY_MODULES]
    finally:
        sys.path.remove(str(scripts))

    assert [module.__name__ for module in modules] == [
        f"sideloadedipa.legacy.{name}" for name in LEGACY_MODULES
    ]


def test_direct_script_bootstraps_src_package_without_site_installation() -> None:
    repository = Path(__file__).parent.parent

    result = subprocess.run(
        [sys.executable, "-S", "scripts/build_backend_qualification_fixture.py", "--help"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "--source-ipa" in result.stdout
