"""Tests for CLI parsing and dependency-injected command routing."""

from __future__ import annotations

import io
import json
import plistlib
import sys
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from sideloadedipa.adapters.publication.icons import build_icon_png
from sideloadedipa.adapters.publication.r2_store import R2Store
from sideloadedipa.application import (
    Application,
    CommandName,
    CommandRequest,
    CommandResult,
    OutputFormat,
)
from sideloadedipa.cli import main
from sideloadedipa.domain import FrozenJsonObject


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


@pytest.fixture(autouse=True)
def isolate_github_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep parser-default assertions independent of the CI runner environment."""

    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)


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


def test_sync_json_preserves_embedded_resource_plan_document() -> None:
    handler = RecordingUseCase()
    handler_result = CommandResult(
        payload=(
            ("status", "applied"),
            (
                "resource_plan",
                FrozenJsonObject(
                    (
                        ("apply", False),
                        ("command", "plan"),
                        ("status", "ready"),
                    )
                ),
            ),
        )
    )
    stdout = io.StringIO()

    def sync(_request: CommandRequest) -> CommandResult:
        return handler_result

    app = application(handler)
    app = Application(
        inspect=app.inspect,
        plan=app.plan,
        sync=sync,
        sign=app.sign,
        verify=app.verify,
        publish=app.publish,
        run=app.run,
    )

    exit_code = main(["sync", "--apply", "--json"], application=app, stdout=stdout)

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["resource_plan"] == {
        "apply": False,
        "command": "plan",
        "status": "ready",
    }


@pytest.mark.parametrize("command", ["publish", "run"])
@pytest.mark.parametrize("icon_source", ["ipa:", "https://example.com/icon.png"])
def test_publication_json_stdout_excludes_adapter_logs(
    command: str,
    icon_source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    icon = io.BytesIO()
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(icon, format="PNG")
    monkeypatch.setattr(
        "sideloadedipa.adapters.publication.icons.fetch_bytes", lambda url: icon.getvalue()
    )
    ipa = tmp_path / "Example.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr(
            "Payload/Example.app/Info.plist",
            plistlib.dumps(
                {"CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon"]}}}
            ),
        )
        archive.writestr("Payload/Example.app/AppIcon.png", icon.getvalue())
    client = MagicMock()
    client.delete_objects.return_value = {}
    store = R2Store(
        "fixture", "fixture", "fixture", "fixture", "https://ipa.example.com", client=client
    )

    def publish(request: CommandRequest) -> CommandResult:
        png = build_icon_png(icon_source, None, ipa_path=ipa)
        store.upload_ipa(ipa, "apps/example/1.0/Example.ipa")
        store.upload_icon("example", png)
        store.upload_json("site/apps.json", {"apps": []})
        store.delete_keys(["apps/example/0.9/Example.ipa"])
        return CommandResult(payload=(("command", request.command.value), ("status", "passed")))

    app = replace(application(RecordingUseCase()), publish=publish, run=publish)
    args = [command, "--json"]
    if command == "run":
        args.extend(["--apply", "--publish"])
    assert main(args, application=app, stdout=sys.stdout, stderr=sys.stderr) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"command": command, "status": "passed"}
    assert len(captured.out.splitlines()) == 1
    for message in (
        "Icon source:" if icon_source == "ipa:" else "Fetching icon:",
        "Icon normalised:",
        "Uploaded:",
        "Uploaded icon:",
        "Uploaded JSON:",
        "Deleted object:",
    ):
        assert f"[info] {message}" in captured.err


def test_default_application_returns_typed_error() -> None:
    stderr = io.StringIO()

    exit_code = main(["sign", "--json"], stderr=stderr)

    assert exit_code == 2
    assert json.loads(stderr.getvalue())["code"] == "config.missing"
