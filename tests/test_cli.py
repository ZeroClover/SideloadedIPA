"""Tests for CLI parsing and dependency-injected command routing."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sideloadedipa.application import (
    Application,
    CommandName,
    CommandRequest,
    CommandResult,
    OutputFormat,
)
from sideloadedipa.cli import main


@dataclass
class RecordingUseCase:
    requests: list[CommandRequest] = field(default_factory=list)

    def __call__(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        return CommandResult(
            human_output=f"handled {request.command.value}",
            payload=(("command", request.command.value),),
        )


def application(handler: RecordingUseCase) -> Application:
    return Application(
        inspect=handler,
        plan=handler,
        sync=handler,
        sign=handler,
        verify=handler,
        publish=handler,
        run=handler,
    )


@pytest.mark.parametrize("command", list(CommandName))
def test_each_command_routes_to_injected_use_case(command: CommandName) -> None:
    handler = RecordingUseCase()
    stdout = io.StringIO()

    exit_code = main(
        [command.value, "--task", "App"], application=application(handler), stdout=stdout
    )

    assert exit_code == 0
    assert handler.requests == [
        CommandRequest(
            command=command,
            config_path=Path("configs/tasks.toml"),
            task_names=("App",),
            output_format=OutputFormat.HUMAN,
        )
    ]
    assert stdout.getvalue().strip() == f"handled {command.value}"


def test_run_parses_apply_publish_and_json_without_executing_business_logic() -> None:
    handler = RecordingUseCase()
    stdout = io.StringIO()

    exit_code = main(
        ["run", "--apply", "--publish", "--json", "--config", "custom.toml"],
        application=application(handler),
        stdout=stdout,
    )

    assert exit_code == 0
    assert handler.requests[0].apply is True
    assert handler.requests[0].publish is True
    assert str(handler.requests[0].config_path) == "custom.toml"
    assert json.loads(stdout.getvalue()) == {"command": "run"}


def test_default_application_returns_typed_error() -> None:
    stderr = io.StringIO()

    exit_code = main(["sign", "--json"], stderr=stderr)

    assert exit_code == 2
    assert json.loads(stderr.getvalue())["code"] == "config.missing"
