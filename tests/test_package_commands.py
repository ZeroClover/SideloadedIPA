"""Tests for the non-publishing package signing command."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import sideloadedipa.package_commands as commands
from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.inspection import ResolvedSource
from sideloadedipa.package_commands import PackageCommandDependencies, sign_command
from sideloadedipa.sources import DownloadedSource


def request(*task_names: str) -> CommandRequest:
    return CommandRequest(
        CommandName.SIGN,
        Path("configs/tasks.toml"),
        task_names,
        OutputFormat.JSON,
    )


def environment() -> dict[str, str]:
    return {
        "APPLE_DEV_CERT_P12_ENCODED": base64.b64encode(b"p12").decode(),
        "APPLE_DEV_CERT_PASSWORD": "secret",
        "GITHUB_TOKEN": "token",
        "ZSIGN_BIN": "/tools/zsign",
        "ZSIGN_SHA256": "a" * 64,
    }


def test_signs_and_verifies_selected_task_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_content = b"source"
    artifact_content = b"signed"
    seen: dict[str, object] = {}

    def resolve(task, dependencies, token):
        assert task.task_name == "LiveContainer"
        assert token == "token"
        return ResolvedSource("https://example/source.ipa", None, {}, None)

    def download(url, destination, *, expected_sha256):
        assert url == "https://example/source.ipa"
        assert expected_sha256 is None
        destination.write_bytes(source_content)
        return DownloadedSource(
            destination,
            len(source_content),
            hashlib.sha256(source_content).hexdigest(),
        )

    def run(**kwargs):
        seen.update(kwargs)
        seen["p12_content"] = kwargs["p12_path"].read_bytes()
        kwargs["destination_ipa"].write_bytes(artifact_content)
        return SimpleNamespace(
            plan=SimpleNamespace(graph_sha256="b" * 64, plan_sha256="c" * 64),
            execution=SimpleNamespace(verification=SimpleNamespace(report_sha256="d" * 64)),
        )

    monkeypatch.setattr(commands, "resolve_source", resolve)
    monkeypatch.setattr(commands, "download_source_asset", download)
    monkeypatch.setattr(commands, "run_package_signing", run)
    dependencies = PackageCommandDependencies(
        profile_root=tmp_path / "profiles",
        output_root=tmp_path / "signed",
        environment=environment(),
    )

    result = sign_command(request("LiveContainer"), dependencies)
    report = dict(result.payload)

    assert result.exit_code == 0
    assert report["status"] == "passed"
    task_report = dict(report["tasks"][0].items)
    assert task_report["publication"] == "disabled"
    assert task_report["artifact_sha256"] == hashlib.sha256(artifact_content).hexdigest()
    assert seen["profile_root"] == tmp_path / "profiles"
    assert seen["p12_content"] == b"p12"


@pytest.mark.parametrize(
    "task_names, environment_override, code",
    [
        (("Unknown",), environment(), ErrorCode.CONFIG_INVALID),
        (("LiveContainer",), {}, ErrorCode.CONFIG_MISSING),
        (
            ("LiveContainer",),
            {**environment(), "APPLE_DEV_CERT_P12_ENCODED": "not-base64"},
            ErrorCode.CONFIG_INVALID,
        ),
    ],
)
def test_rejects_invalid_selection_or_private_inputs(
    tmp_path: Path,
    task_names: tuple[str, ...],
    environment_override: dict[str, str],
    code: ErrorCode,
) -> None:
    dependencies = PackageCommandDependencies(
        profile_root=tmp_path / "profiles",
        output_root=tmp_path / "signed",
        environment=environment_override,
    )

    with pytest.raises(ConfigurationError) as caught:
        sign_command(request(*task_names), dependencies)

    assert caught.value.code is code
