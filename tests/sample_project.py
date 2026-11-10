"""A task configuration owned by the tests.

Tests take their sample tasks and entitlement templates from here, so editing
the production task list under ``configs/`` can never change a test result.
"""

from __future__ import annotations

from pathlib import Path

from sideloadedipa.config import load_configuration
from sideloadedipa.domain import TaskConfiguration

PROJECT_ROOT = Path(__file__).parent / "fixtures" / "project"
CONFIG_PATH = PROJECT_ROOT / "configs" / "tasks.toml"


def sample_configuration() -> TaskConfiguration:
    return load_configuration(CONFIG_PATH)
