"""Shared subprocess execution with bounded, redacted evidence."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode
from sideloadedipa.util.atomics import redact_text

DEFAULT_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)


@dataclass(frozen=True, slots=True)
class SubprocessResult:
    argv: tuple[str, ...]
    stdout: str
    stderr: str
    duration_seconds: float


def _bounded_text(value: bytes | str | None, limit: int, redactions: Sequence[str]) -> str:
    if value is None:
        return ""
    decoded = value if isinstance(value, str) else value.decode("utf-8", errors="replace")
    encoded = redact_text(decoded, redactions).encode()
    if limit <= 0:
        bounded = b""
    elif len(encoded) <= limit:
        bounded = encoded
    else:
        head = limit // 2
        bounded = encoded[:head] + encoded[-(limit - head) :]
    return bounded.decode("utf-8", errors="replace")


class SubprocessRunner:
    def __init__(
        self,
        *,
        allowed_environment: Iterable[str] = DEFAULT_ENV_ALLOWLIST,
        default_timeout_seconds: float = 120,
        max_output_bytes: int = 64 * 1024,
        max_success_output_bytes: int | None = None,
    ) -> None:
        self.allowed_environment = frozenset(allowed_environment)
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_success_output_bytes = (
            max_output_bytes if max_success_output_bytes is None else max_success_output_bytes
        )

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
    ) -> SubprocessResult:
        if not argv:
            raise ConfigurationError(ErrorCode.CONFIG_INVALID, "subprocess argv is empty")

        disallowed = sorted(set(environment or ()) - self.allowed_environment)
        if disallowed:
            raise ConfigurationError(
                ErrorCode.CONFIG_INVALID,
                "subprocess environment contains non-allowlisted keys",
                safe_details=(("keys", tuple(disallowed)),),
            )

        command = tuple(os.fspath(value) for value in argv)
        redactions = (
            *secret_redactions,
            *(os.fspath(path) for path in path_redactions),
        )
        safe_argv = tuple(redact_text(value, redactions) for value in command)
        child_environment = {
            key: value for key, value in os.environ.items() if key in self.allowed_environment
        }
        child_environment.update(environment or {})
        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        started = time.monotonic()

        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=child_environment,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise AdapterError(
                ErrorCode.ADAPTER_TIMEOUT,
                "subprocess timed out",
                adapter=Path(command[0]).name,
                operation="execute",
                safe_details=(
                    ("argv", safe_argv),
                    ("timeout_seconds", timeout),
                    ("stdout", _bounded_text(error.stdout, self.max_output_bytes, redactions)),
                    ("stderr", _bounded_text(error.stderr, self.max_output_bytes, redactions)),
                ),
            ) from error
        except OSError as error:
            raise AdapterError(
                ErrorCode.ADAPTER_UNAVAILABLE,
                "subprocess could not be started",
                adapter=Path(command[0]).name,
                operation="execute",
                safe_details=(("argv", safe_argv), ("os_error", type(error).__name__)),
            ) from error

        stdout = _bounded_text(completed.stdout, self.max_success_output_bytes, redactions)
        stderr = _bounded_text(completed.stderr, self.max_output_bytes, redactions)
        duration = time.monotonic() - started
        if completed.returncode != 0:
            raise AdapterError(
                ErrorCode.ADAPTER_COMMAND_FAILED,
                "subprocess exited nonzero",
                adapter=Path(command[0]).name,
                operation="execute",
                safe_details=(
                    ("argv", safe_argv),
                    ("exit_code", completed.returncode),
                    (
                        "stdout",
                        _bounded_text(completed.stdout, self.max_output_bytes, redactions),
                    ),
                    ("stderr", stderr),
                ),
            )

        return SubprocessResult(
            argv=safe_argv,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
        )
