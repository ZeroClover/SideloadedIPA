"""Tests for the non-publishing package signing command."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import sideloadedipa.package_commands as commands
from sideloadedipa.application import CommandName, CommandRequest, OutputFormat
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.inspection import ResolvedSource
from sideloadedipa.ipa import IpaMetadata
from sideloadedipa.package_commands import PackageCommandDependencies, run_command, sign_command
from sideloadedipa.sources import DownloadedSource


def request(*task_names: str) -> CommandRequest:
    return CommandRequest(
        CommandName.SIGN,
        Path("configs/tasks.toml"),
        task_names,
        OutputFormat.JSON,
    )


def run_request(*task_names: str, publish: bool) -> CommandRequest:
    return CommandRequest(
        CommandName.RUN,
        Path("configs/tasks.toml"),
        task_names,
        OutputFormat.JSON,
        publish=publish,
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


def test_run_rejects_publication_disabled_task_before_signing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = commands.load_configuration(Path("configs/tasks.toml"))
    livecontainer = next(task for task in configuration.tasks if task.task_name == "LiveContainer")
    disabled_configuration = replace(
        configuration,
        tasks=(replace(livecontainer, publication_enabled=False),),
    )
    monkeypatch.setattr(commands, "load_configuration", lambda _: disabled_configuration)
    monkeypatch.setattr(
        commands,
        "_sign_tasks",
        lambda *args: pytest.fail("disabled task reached signing"),
    )
    dependencies = PackageCommandDependencies(
        output_root=tmp_path / "signed",
        environment=environment(),
    )

    with pytest.raises(ConfigurationError, match="not approved") as caught:
        run_command(run_request("LiveContainer", publish=True), dependencies)

    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_run_publishes_only_after_the_complete_verified_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = commands.load_configuration(Path("configs/tasks.toml"))
    tasks = tuple(task for task in configuration.tasks if task.task_name in {"JHenTai", "Asspp"})
    events: list[str] = []
    signed = tuple(
        commands._SignedTask(
            task,
            f"v{index}",
            {
                "version": f"v{index}",
                "published_at": f"2026-07-{index:02d}T00:00:00Z",
                "download_url": f"https://example.test/{task.slug}.ipa",
                "asset_id": str(index),
            },
            str(index) * 64,
            tmp_path / f"{task.slug}.ipa",
            chr(96 + index) * 64,
            SimpleNamespace(
                plan=SimpleNamespace(
                    graph_sha256=chr(97 + index) * 64,
                    plan_sha256=chr(98 + index) * 64,
                ),
                execution=SimpleNamespace(
                    verification=SimpleNamespace(report_sha256=chr(99 + index) * 64)
                ),
            ),
        )
        for index, task in enumerate(tasks, start=1)
    )
    for value in signed:
        value.artifact_path.write_bytes(b"signed")

    def sign_all(*args):
        events.append("signed-batch")
        return signed

    class Store:
        def upload_icon(self, slug, png):
            print(f"uploading {slug}")
            events.append(f"icon:{slug}")
            return f"https://cdn.example/{slug}/icon.png"

    class Publisher:
        def publish(self, candidates, *, now):
            del now
            print("publishing registry")
            assert events[0] == "signed-batch"
            events.append("published-batch")
            return tuple(
                SimpleNamespace(
                    task_name=value.task_name,
                    artifact_key=f"apps/{value.slug}/{value.filename}",
                    artifact_url=f"https://cdn.example/{value.slug}/{value.filename}",
                    registry_key="site/apps.json",
                    registry_sha256="f" * 64,
                    stale_keys_removed=(),
                )
                for value in candidates
            )

    monkeypatch.setattr(commands, "_sign_tasks", sign_all)
    monkeypatch.setattr(commands, "_publication_runtime", lambda *args: (Store(), Publisher()))
    monkeypatch.setattr(
        commands,
        "read_ipa_metadata",
        lambda path: IpaMetadata(f"io.example.{path.stem.lower()}", "1.2.3"),
    )
    monkeypatch.setattr(commands, "build_icon_png", lambda *args, **kwargs: b"png")
    dependencies = PackageCommandDependencies(
        output_root=tmp_path,
        cache_root=tmp_path / "cache",
        environment=environment(),
    )

    result = run_command(run_request("JHenTai", "Asspp", publish=True), dependencies)
    report = dict(result.payload)

    assert result.exit_code == 0
    assert report["task_count"] == 2
    assert events[-1] == "published-batch"
    assert events.count("published-batch") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "publishing registry" in captured.err
    cache = json.loads((tmp_path / "cache" / "release-versions.json").read_text())
    assert set(cache["tasks"]) == {"JHenTai", "Asspp"}


def test_revalidation_encodes_secret_and_preserves_existing_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def open_url(request, *, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(commands.urllib.request, "urlopen", open_url)

    assert commands._trigger_revalidation(
        {
            "VERCEL_REVALIDATE_SECRET": "a secret&value",
            "VERCEL_REVALIDATE_URL": "https://example.test/revalidate?scope=apps",
        }
    )
    assert seen == {
        "url": "https://example.test/revalidate?scope=apps&secret=a+secret%26value",
        "timeout": 30,
    }


def test_revalidation_reports_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        commands.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert not commands._trigger_revalidation({"VERCEL_REVALIDATE_SECRET": "secret"})


def test_publication_runtime_reports_missing_r2_credentials() -> None:
    configuration = commands.load_configuration(Path("configs/tasks.toml"))

    with pytest.raises(ConfigurationError, match="R2 credentials") as caught:
        commands._publication_runtime(
            configuration,
            {"VERCEL_REVALIDATE_SECRET": "secret"},
        )

    assert caught.value.code is ErrorCode.CONFIG_MISSING


def test_release_cache_rejects_corrupt_predecessor(tmp_path: Path) -> None:
    path = tmp_path / "release-versions.json"
    path.write_text("{not-json")

    assert not commands._update_release_cache(
        path,
        (),
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert path.read_text() == "{not-json"
