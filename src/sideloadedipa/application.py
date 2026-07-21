"""Application command requests and dependency-injected use-case routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from sideloadedipa.domain.common import FrozenJsonValue


class CommandName(StrEnum):
    INSPECT = "inspect"
    PLAN = "plan"
    SYNC = "sync"
    SIGN = "sign"
    VERIFY = "verify"
    RUN = "run"


class OutputFormat(StrEnum):
    HUMAN = "human"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    command: CommandName
    config_path: Path
    task_names: tuple[str, ...]
    output_format: OutputFormat
    apply: bool = False
    publish: bool = False


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int = 0
    human_output: str | None = None
    payload: tuple[tuple[str, FrozenJsonValue], ...] = ()


class CommandUseCase(Protocol):
    def __call__(self, request: CommandRequest) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class Application:
    inspect: CommandUseCase
    plan: CommandUseCase
    sync: CommandUseCase
    sign: CommandUseCase
    verify: CommandUseCase
    run: CommandUseCase

    def execute(self, request: CommandRequest) -> CommandResult:
        handler = {
            CommandName.INSPECT: self.inspect,
            CommandName.PLAN: self.plan,
            CommandName.SYNC: self.sync,
            CommandName.SIGN: self.sign,
            CommandName.VERIFY: self.verify,
            CommandName.RUN: self.run,
        }[request.command]
        return handler(request)
