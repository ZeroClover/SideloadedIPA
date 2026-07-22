"""Tests for the installable package and console entry point."""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from sideloadedipa import __version__
from sideloadedipa.cli import main


def test_distribution_exposes_console_entry_point() -> None:
    scripts = entry_points(group="console_scripts")

    assert any(
        entry.name == "sideloadedipa" and entry.value == "sideloadedipa.cli:main"
        for entry in scripts
    )


def test_cli_reports_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == f"sideloadedipa {__version__}"
