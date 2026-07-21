"""Command-line entry point for the typed pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from sideloadedipa import __version__
from sideloadedipa.apple_commands import plan_command, sync_command
from sideloadedipa.application import (
    Application,
    CommandName,
    CommandRequest,
    CommandResult,
    OutputFormat,
)
from sideloadedipa.domain import thaw_json
from sideloadedipa.errors import ConfigurationError, ErrorCode, SideloadedIPAError
from sideloadedipa.inspection import inspect_command
from sideloadedipa.package_commands import sign_command


def _unconfigured(request: CommandRequest) -> CommandResult:
    raise ConfigurationError(
        ErrorCode.CONFIG_MISSING,
        f"{request.command.value} dependencies are not configured",
        remediation="compose the command use case in the application entry point",
    )


def default_application() -> Application:
    return Application(
        inspect=inspect_command,
        plan=plan_command,
        sync=sync_command,
        sign=sign_command,
        verify=_unconfigured,
        run=_unconfigured,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sideloadedipa")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in CommandName:
        command_parser = subparsers.add_parser(command.value)
        command_parser.add_argument("--config", type=Path, default=Path("configs/tasks.toml"))
        command_parser.add_argument("--task", action="append", default=[])
        command_parser.add_argument("--json", action="store_true")
        if command in {CommandName.SYNC, CommandName.RUN}:
            command_parser.add_argument("--apply", action="store_true")
        if command is CommandName.RUN:
            command_parser.add_argument("--publish", action="store_true")
    return parser


def _request(namespace: argparse.Namespace) -> CommandRequest:
    return CommandRequest(
        command=CommandName(namespace.command),
        config_path=namespace.config,
        task_names=tuple(namespace.task),
        output_format=OutputFormat.JSON if namespace.json else OutputFormat.HUMAN,
        apply=getattr(namespace, "apply", False),
        publish=getattr(namespace, "publish", False),
    )


def _write_result(result: CommandResult, output_format: OutputFormat, stdout: TextIO) -> None:
    if output_format is OutputFormat.JSON:
        print(
            json.dumps(
                {key: thaw_json(value) for key, value in result.payload},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=stdout,
        )
    elif result.human_output:
        print(result.human_output, file=stdout)


def _write_error(error: SideloadedIPAError, output_format: OutputFormat, stderr: TextIO) -> None:
    diagnostic = error.to_diagnostic()
    if output_format is OutputFormat.JSON:
        print(
            json.dumps(
                {
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                    "task_name": diagnostic.task_name,
                    "bundle_id": diagnostic.bundle_id,
                    "remediation": diagnostic.remediation,
                    "details": dict(diagnostic.details),
                },
                sort_keys=True,
            ),
            file=stderr,
        )
    else:
        print(f"[{diagnostic.code}] {diagnostic.message}", file=stderr)


def main(
    argv: Sequence[str] | None = None,
    *,
    application: Application | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    request = _request(parser.parse_args(argv))
    try:
        result = (application or default_application()).execute(request)
    except SideloadedIPAError as error:
        _write_error(error, request.output_format, stderr)
        return 2
    _write_result(result, request.output_format, stdout)
    return result.exit_code
