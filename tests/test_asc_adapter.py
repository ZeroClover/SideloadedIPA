"""Contract tests for the pinned App Store Connect CLI adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from sideloadedipa.adapters.apple import AscClient
from sideloadedipa.domain import thaw_json
from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode
from sideloadedipa.subprocesses import SubprocessResult

CONTRACT = Path(__file__).parent / "fixtures" / "asc" / "3.1.1-contract.json"


class RecordingRunner:
    def __init__(self, outcomes: Sequence[SubprocessResult | AdapterError]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "argv": tuple(os.fspath(value) for value in argv),
                "cwd": cwd,
                "environment": dict(environment or {}),
                "timeout_seconds": timeout_seconds,
                "input_bytes": input_bytes,
                "secret_redactions": tuple(secret_redactions),
                "path_redactions": tuple(path_redactions),
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, AdapterError):
            raise outcome
        return outcome


def result(stdout: str, argv: tuple[str, ...] = ("asc",)) -> SubprocessResult:
    return SubprocessResult(argv=argv, stdout=stdout, stderr="", duration_seconds=0.25)


def command_error(exit_code: int) -> AdapterError:
    return AdapterError(
        ErrorCode.ADAPTER_COMMAND_FAILED,
        "subprocess exited nonzero",
        adapter="asc",
        operation="execute",
        safe_details=(
            ("argv", ("asc", "devices", "list", "--output", "json")),
            ("exit_code", exit_code),
            ("stdout", ""),
            ("stderr", "Error: redacted fixture"),
        ),
    )


def test_contract_fixture_tracks_verified_release_and_has_no_credentials() -> None:
    contract = json.loads(CONTRACT.read_text())

    assert contract["upstream"]["tag"] == "3.1.1"
    assert contract["tools"]["linux_amd64_sha256"] == (
        "57cca59153eda109faf18d72c8bb0989ed0ee6e0a3082ce73ffa08174afbf2fd"
    )
    assert contract["retry_contract"] == {
        "methods": ["GET", "HEAD"],
        "statuses": [408, 429, 500, 502, 503, 504],
        "max_retries": 3,
        "base_delay": "1s",
        "max_delay": "10s",
        "jitter": "plus-or-minus-25-percent",
        "honors_retry_after": True,
    }
    serialized = CONTRACT.read_text()
    assert "BEGIN PRIVATE KEY" not in serialized
    assert "AuthKey_" not in serialized


def test_versioned_paginated_json_command_is_canonical_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = json.loads(CONTRACT.read_text())
    monkeypatch.setenv("ASC_KEY_ID", "KEY_SECRET")
    monkeypatch.setenv("ASC_ISSUER_ID", "ISSUER_SECRET")
    monkeypatch.setenv("ASC_PRIVATE_KEY_B64", "PRIVATE_KEY_SECRET")
    monkeypatch.setenv("ASC_PRIVATE_KEY_PATH", "/private/key/AuthKey.p8")
    runner = RecordingRunner(
        (
            result(contract["tools"]["version_output"], ("asc", "version")),
            result(
                json.dumps(contract["list_contract"]["response"]),
                ("asc", *contract["list_contract"]["argv"]),
            ),
            result('{"data":[]}', ("asc", "devices", "list", "--output", "json")),
        )
    )
    client = AscClient(runner=runner)

    response = client.run_json(
        ("devices", "list", "--platform", "IOS"),
        paginate=True,
    )
    client.run_json(("devices", "list"))

    assert response.document is not None
    document = thaw_json(response.document)
    assert document == contract["list_contract"]["response"]
    assert runner.calls[0]["argv"] == ("asc", "version")
    assert runner.calls[1]["argv"] == ("asc", *contract["list_contract"]["argv"])
    assert runner.calls[2]["argv"] == ("asc", "devices", "list", "--output", "json")
    assert runner.calls[1]["environment"] == {
        "ASC_BASE_DELAY": "1s",
        "ASC_BYPASS_KEYCHAIN": "1",
        "ASC_MAX_DELAY": "10s",
        "ASC_MAX_RETRIES": "3",
        "ASC_TELEMETRY_DISABLED": "1",
    }
    redactions = runner.calls[1]["secret_redactions"]
    assert isinstance(redactions, tuple)
    assert "KEY_SECRET" in redactions
    assert "ISSUER_SECRET" in redactions
    assert "PRIVATE_KEY_SECRET" in redactions
    assert runner.calls[1]["path_redactions"] == (Path("/private/key/AuthKey.p8"),)


def test_rejects_wrong_version_before_business_command() -> None:
    runner = RecordingRunner((result("3.2.0 (commit: future)"),))

    with pytest.raises(AdapterError) as caught:
        AscClient(runner=runner).run_json(("devices", "list"))

    assert caught.value.code is ErrorCode.ADAPTER_VERSION_MISMATCH
    assert caught.value.safe_details[-2:] == (
        ("expected_version", "3.1.1"),
        ("actual_version", "3.2.0"),
    )
    assert len(runner.calls) == 1


@pytest.mark.parametrize("stdout", ("", "not json", "[]"))
def test_rejects_empty_malformed_or_non_object_json(stdout: str) -> None:
    runner = RecordingRunner((result("3.1.1"), result(stdout)))

    with pytest.raises(AdapterError) as caught:
        AscClient(runner=runner).run_json(("profiles", "list"))

    assert caught.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID


def test_allows_explicitly_empty_delete_response() -> None:
    runner = RecordingRunner((result("3.1.1"), result("")))

    response = AscClient(runner=runner).run_json(
        ("profiles", "delete", "--id", "PROFILE", "--confirm"),
        allow_empty=True,
    )

    assert response.document is None


@pytest.mark.parametrize(
    ("exit_code", "expected_code"),
    [
        (2, ErrorCode.CONFIG_INVALID),
        (3, ErrorCode.APPLE_AUTHORIZATION_FAILED),
        (4, ErrorCode.APPLE_RESOURCE_NOT_FOUND),
        (5, ErrorCode.APPLE_RESOURCE_CONFLICT),
        (39, ErrorCode.APPLE_RATE_LIMITED),
        (63, ErrorCode.ADAPTER_UNAVAILABLE),
        (22, ErrorCode.APPLE_API_FAILED),
    ],
)
def test_maps_documented_asc_exit_codes(exit_code: int, expected_code: ErrorCode) -> None:
    runner = RecordingRunner((result("3.1.1"), command_error(exit_code)))

    with pytest.raises(AdapterError) as caught:
        AscClient(runner=runner).run_json(("devices", "list"), paginate=True)

    assert caught.value.code is expected_code
    assert dict(caught.value.safe_details)["exit_code"] == exit_code
    assert caught.value.adapter == "asc"
    assert caught.value.operation == "devices-list"


def test_authorization_failure_directs_operator_to_roles_and_agreements() -> None:
    runner = RecordingRunner((result("3.1.1"), command_error(3)))

    with pytest.raises(AdapterError) as caught:
        AscClient(runner=runner).run_json(("profiles", "list"), paginate=True)

    assert caught.value.code is ErrorCode.APPLE_AUTHORIZATION_FAILED
    assert "agreements" in (caught.value.remediation or "")
    assert "role" in (caught.value.remediation or "")


@pytest.mark.parametrize("args", ((), ("devices", "list", "--paginate")))
def test_rejects_missing_or_adapter_owned_arguments(args: tuple[str, ...]) -> None:
    client = AscClient(runner=RecordingRunner(()))

    with pytest.raises(ConfigurationError):
        client.run_json(args)
