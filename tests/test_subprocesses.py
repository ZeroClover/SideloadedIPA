"""Tests for the shared subprocess runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode
from sideloadedipa.subprocesses import SubprocessRunner


def test_metacharacters_are_passed_as_one_literal_argument(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    argument = f"space ; $(touch {marker}) ' quote"
    runner = SubprocessRunner()

    result = runner.run(
        [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", argument]
    )

    assert json.loads(result.stdout) == [argument]
    assert marker.exists() is False


def test_environment_is_allowlisted_and_overrides_are_checked() -> None:
    runner = SubprocessRunner(allowed_environment={"VISIBLE"})

    result = runner.run(
        [sys.executable, "-c", "import os; print(os.getenv('VISIBLE'), os.getenv('HOME'))"],
        environment={"VISIBLE": "yes"},
    )

    assert result.stdout.strip() == "yes None"
    with pytest.raises(ConfigurationError) as raised:
        runner.run([sys.executable, "-c", "pass"], environment={"HIDDEN": "no"})
    assert raised.value.code is ErrorCode.CONFIG_INVALID


def test_failure_output_is_bounded_and_redacted(tmp_path: Path) -> None:
    secret = "private-password"
    private_path = tmp_path / "private" / "profile.mobileprovision"
    runner = SubprocessRunner(max_output_bytes=128)
    script = "import sys; print('x' * 256 + sys.argv[1] + sys.argv[2]); raise SystemExit(7)"

    with pytest.raises(AdapterError) as raised:
        runner.run(
            [sys.executable, "-c", script, secret, str(private_path)],
            secret_redactions=[secret],
            path_redactions=[private_path],
        )

    error = raised.value
    details = dict(error.safe_details)
    assert error.code is ErrorCode.ADAPTER_COMMAND_FAILED
    assert details["exit_code"] == 7
    assert secret not in str(details)
    assert str(private_path) not in str(details)
    assert "***" in str(details)
    assert len(details["stdout"]) <= 128


def test_success_output_is_complete_and_redacted() -> None:
    secret = "private-password"
    runner = SubprocessRunner(max_output_bytes=128)
    prefix = "x" * 256

    result = runner.run(
        [sys.executable, "-c", "import sys; print(sys.argv[1] + sys.argv[2])", prefix, secret],
        secret_redactions=[secret],
    )

    assert result.stdout == f"{prefix}***\n"


def test_timeout_and_missing_executable_have_stable_codes() -> None:
    runner = SubprocessRunner(default_timeout_seconds=0.01)

    with pytest.raises(ConfigurationError) as empty:
        runner.run([])
    assert empty.value.code is ErrorCode.CONFIG_INVALID

    with pytest.raises(AdapterError) as timeout:
        runner.run([sys.executable, "-c", "import time; time.sleep(1)"])
    assert timeout.value.code is ErrorCode.ADAPTER_TIMEOUT

    with pytest.raises(AdapterError) as unavailable:
        runner.run(["definitely-not-a-real-sideloadedipa-command"])
    assert unavailable.value.code is ErrorCode.ADAPTER_UNAVAILABLE
