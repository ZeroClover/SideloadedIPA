"""Typed App Store Connect CLI execution and JSON decoding."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sideloadedipa.domain import FrozenJsonObject, FrozenJsonValue, freeze_json
from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode
from sideloadedipa.subprocesses import (
    DEFAULT_ENV_ALLOWLIST,
    SubprocessResult,
    SubprocessRunner,
)

SUPPORTED_ASC_VERSION = "3.1.1"
_ASC_MAX_SUCCESS_OUTPUT_BYTES = 16 * 1024 * 1024

_ASC_CREDENTIAL_ENV = frozenset(
    {
        "ASC_ISSUER_ID",
        "ASC_KEY_ID",
        "ASC_KEY_TYPE",
        "ASC_PRIVATE_KEY",
        "ASC_PRIVATE_KEY_B64",
        "ASC_PRIVATE_KEY_PATH",
    }
)
_ASC_RUNTIME_ENV = frozenset(
    {
        "ASC_BASE_DELAY",
        "ASC_BYPASS_KEYCHAIN",
        "ASC_MAX_DELAY",
        "ASC_MAX_RETRIES",
        "ASC_TELEMETRY_DISABLED",
    }
)
_ASC_ENVIRONMENT = DEFAULT_ENV_ALLOWLIST | _ASC_CREDENTIAL_ENV | _ASC_RUNTIME_ENV


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        input_bytes: bytes | None = None,
        secret_redactions: Sequence[str] = (),
        path_redactions: Sequence[Path] = (),
    ) -> SubprocessResult: ...


@dataclass(frozen=True, slots=True)
class AscToolIdentity:
    version: str
    raw_version: str


@dataclass(frozen=True, slots=True)
class AscResponse:
    document: FrozenJsonObject | None
    argv: tuple[str, ...]
    duration_seconds: float


def _error_details(error: AdapterError) -> dict[str, FrozenJsonValue]:
    return {key: value for key, value in error.safe_details if key not in {"adapter", "operation"}}


def _mapped_error(error: AdapterError, operation: str) -> AdapterError:
    details = _error_details(error)
    exit_code = details.get("exit_code")
    mapped_code = ErrorCode.APPLE_API_FAILED
    message = "App Store Connect CLI command failed"
    remediation = "inspect the redacted CLI diagnostic and retry after correcting the cause"
    if exit_code == 2:
        mapped_code = ErrorCode.CONFIG_INVALID
        message = "App Store Connect CLI command is incompatible with the pinned version"
        remediation = "update the adapter command contract for the pinned asc release"
    elif exit_code == 3:
        mapped_code = ErrorCode.APPLE_AUTHORIZATION_FAILED
        message = "App Store Connect authentication or authorization failed"
        remediation = "verify API credentials, agreements, and the key role"
    elif exit_code == 4:
        mapped_code = ErrorCode.APPLE_RESOURCE_NOT_FOUND
        message = "App Store Connect resource was not found"
        remediation = "refresh Apple state and verify the exact resource identifier"
    elif exit_code == 5:
        mapped_code = ErrorCode.APPLE_RESOURCE_CONFLICT
        message = "App Store Connect rejected a conflicting resource operation"
        remediation = "re-list exact resources before deciding whether another create is safe"
    elif exit_code == 39:
        mapped_code = ErrorCode.APPLE_RATE_LIMITED
        message = "App Store Connect rate limit persisted after bounded retries"
        remediation = "retry later without changing the operation identity"
    elif isinstance(exit_code, int) and 60 <= exit_code <= 99:
        mapped_code = ErrorCode.ADAPTER_UNAVAILABLE
        message = "App Store Connect service remained unavailable after bounded retries"
        remediation = "retry the read operation after Apple service recovery"
    return AdapterError(
        mapped_code,
        message,
        adapter="asc",
        operation=operation,
        remediation=remediation,
        safe_details=tuple(details.items()),
    )


class AscClient:
    """Execute the pinned asc CLI with stable output and error contracts."""

    def __init__(
        self,
        *,
        executable: str | os.PathLike[str] = "asc",
        expected_version: str = SUPPORTED_ASC_VERSION,
        runner: CommandRunner | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self.executable = os.fspath(executable)
        self.expected_version = expected_version
        self.runner = runner or SubprocessRunner(
            allowed_environment=_ASC_ENVIRONMENT,
            default_timeout_seconds=timeout_seconds,
            max_success_output_bytes=_ASC_MAX_SUCCESS_OUTPUT_BYTES,
        )
        self.timeout_seconds = timeout_seconds
        self._identity: AscToolIdentity | None = None

    def _redactions(self) -> tuple[tuple[str, ...], tuple[Path, ...]]:
        secrets = tuple(os.environ.get(key, "") for key in sorted(_ASC_CREDENTIAL_ENV))
        path_value = os.environ.get("ASC_PRIVATE_KEY_PATH")
        return secrets, (Path(path_value),) if path_value else ()

    def _run(self, args: Sequence[str], operation: str) -> SubprocessResult:
        secrets, paths = self._redactions()
        try:
            return self.runner.run(
                [self.executable, *args],
                environment={
                    "ASC_BASE_DELAY": "1s",
                    "ASC_BYPASS_KEYCHAIN": "1",
                    "ASC_MAX_DELAY": "10s",
                    "ASC_MAX_RETRIES": "3",
                    "ASC_TELEMETRY_DISABLED": "1",
                },
                timeout_seconds=self.timeout_seconds,
                secret_redactions=secrets,
                path_redactions=paths,
            )
        except AdapterError as error:
            if error.code is ErrorCode.ADAPTER_COMMAND_FAILED:
                raise _mapped_error(error, operation) from error
            raise

    def verify_version(self) -> AscToolIdentity:
        if self._identity is not None:
            return self._identity
        result = self._run(("version",), "version")
        raw_version = result.stdout.strip()
        version = raw_version.split(maxsplit=1)[0] if raw_version else ""
        if version != self.expected_version:
            raise AdapterError(
                ErrorCode.ADAPTER_VERSION_MISMATCH,
                "App Store Connect CLI version does not match the supported release",
                adapter="asc",
                operation="version",
                remediation="install the checksum-verified supported asc release",
                safe_details=(
                    ("expected_version", self.expected_version),
                    ("actual_version", version or None),
                ),
            )
        self._identity = AscToolIdentity(version=version, raw_version=raw_version)
        return self._identity

    def run_json(
        self,
        args: Sequence[str],
        *,
        paginate: bool = False,
        allow_empty: bool = False,
    ) -> AscResponse:
        if not args:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "App Store Connect CLI arguments are empty",
            )
        if any(value in {"--output", "--paginate"} for value in args):
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "App Store Connect output and pagination flags are adapter-owned",
                remediation="pass pagination through the typed adapter option",
            )

        self.verify_version()
        operation = "-".join(args[:2])
        command = [*args]
        if paginate:
            command.append("--paginate")
        command.extend(("--output", "json"))
        result = self._run(command, operation)
        raw = result.stdout.strip()
        if not raw and allow_empty:
            document = None
        else:
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as error:
                raise AdapterError(
                    ErrorCode.ADAPTER_RESPONSE_INVALID,
                    "App Store Connect CLI returned invalid JSON",
                    adapter="asc",
                    operation=operation,
                ) from error
            frozen = freeze_json(decoded)
            if not isinstance(frozen, FrozenJsonObject):
                raise AdapterError(
                    ErrorCode.ADAPTER_RESPONSE_INVALID,
                    "App Store Connect CLI JSON root is not an object",
                    adapter="asc",
                    operation=operation,
                )
            document = frozen
        return AscResponse(
            document=document,
            argv=result.argv,
            duration_seconds=result.duration_seconds,
        )
