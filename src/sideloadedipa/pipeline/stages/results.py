"""Stable public command result construction for production stages."""

from __future__ import annotations

from sideloadedipa.application import CommandResult
from sideloadedipa.domain.common import FrozenJsonObject, freeze_json, thaw_json
from sideloadedipa.errors import DomainError, ErrorCode


def payload_document(result: CommandResult) -> dict[str, object]:
    return {key: thaw_json(value) for key, value in result.payload}


def command_result(
    command: str,
    document: dict[str, object],
    human_output: str,
) -> CommandResult:
    payload = {"schema_version": 1, "command": command, **document}
    frozen = freeze_json(payload)
    if not isinstance(frozen, FrozenJsonObject):
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "production pipeline command report root must be an object",
            remediation="discard the malformed report and rerun the production command",
        )
    return CommandResult(human_output=human_output, payload=frozen.items)
