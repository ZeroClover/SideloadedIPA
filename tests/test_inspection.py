"""Tests for the read-only IPA inspection use case."""

from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.application import (
    Application,
    CommandName,
    CommandRequest,
    CommandResult,
    OutputFormat,
)
from sideloadedipa.cli import main
from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    SourceConfig,
    SourceKind,
    Task,
    TaskConfiguration,
    thaw_json,
)
from sideloadedipa.errors import AdapterError, DomainError, ErrorCode
from sideloadedipa.inspection import InspectDependencies, inspect_command
from sideloadedipa.sources import DownloadedSource


def task(name: str) -> Task:
    return Task(
        task_name=name,
        app_name=name,
        bundle_id=f"io.example.{name.lower()}",
        source=SourceConfig(
            kind=SourceKind.GITHUB_RELEASE,
            location=f"https://github.com/example/{name}",
            release_glob="*.ipa",
        ),
        slug=name,
    )


def graph(source_sha256: str) -> BundleGraph:
    node = BundleNode(
        path=PurePosixPath("Payload/App.app"),
        kind=BundleNodeKind.APP,
        depth=0,
        executable_path=PurePosixPath("Payload/App.app/App"),
        executable_sha256="b" * 64,
        source_bundle_id="com.source.app",
        info_plist_sha256="c" * 64,
        entitlements=(("application-identifier", "OLD.com.source.app"),),
    )
    return BundleGraph(
        root_path=node.path,
        nodes=(node,),
        source_sha256=source_sha256,
        graph_sha256="d" * 64,
    )


def dependencies(
    tasks: tuple[Task, ...],
    *,
    fail_name: str | None = None,
    fail_graph: bool = False,
    structure_calls: list[str] | None = None,
) -> InspectDependencies:
    def fetch(repository_url: str, **kwargs: object) -> dict[str, object]:
        name = repository_url.rsplit("/", 1)[-1]
        assert kwargs["token"] == "fixture-token"
        return {
            "tag_name": "v1",
            "assets": [
                {
                    "id": name,
                    "name": f"{name}.ipa",
                    "browser_download_url": f"https://download.example/{name}.ipa?token=secret",
                    "size": 3,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }

    def download(url: str, destination: Path, **kwargs: object) -> DownloadedSource:
        name = Path(url.split("?", 1)[0]).stem
        if name == fail_name:
            raise AdapterError(
                ErrorCode.SOURCE_DOWNLOAD_FAILED,
                "fixture download failed",
                adapter="fixture",
                operation="download",
            )
        assert kwargs["expected_sha256"] == "sha256:" + "a" * 64
        destination.write_bytes(b"ipa")
        return DownloadedSource(destination, 3, "a" * 64)

    def discover(extracted: Path, source_sha256: str) -> BundleGraph:
        if fail_graph:
            raise DomainError(
                ErrorCode.INVENTORY_ENTITLEMENTS_INVALID,
                "fixture has no entitlement evidence",
                bundle_id="com.source.app",
                safe_details=(("path", "Payload/App.app/App"),),
            )
        return graph(source_sha256)

    def discover_structure(extracted_root: Path) -> tuple[BundleNode, ...]:
        if structure_calls is not None:
            structure_calls.append(str(extracted_root))
        return graph("a" * 64).nodes

    return InspectDependencies(
        load=lambda path: TaskConfiguration(tasks),
        fetch_release=fetch,
        download=download,
        extract=lambda source, destination: (object(), object()),
        discover_structure=discover_structure,
        discover=discover,  # type: ignore[arg-type]
    )


def app_for(handler: object) -> Application:
    def unavailable(request: CommandRequest) -> CommandResult:
        raise AssertionError(request.command)

    return Application(
        inspect=handler,  # type: ignore[arg-type]
        plan=unavailable,
        sync=unavailable,
        sign=unavailable,
        verify=unavailable,
        publish=unavailable,
        run=unavailable,
    )


def test_inspect_json_is_canonical_redacted_and_selectable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    configured = (task("First"), task("Second"))
    deps = dependencies(configured)
    stdout = io.StringIO()

    exit_code = main(
        ["inspect", "--json", "--config", str(tmp_path / "tasks.toml"), "--task", "Second"],
        application=app_for(lambda request: inspect_command(request, deps)),
        stdout=stdout,
    )

    assert exit_code == 0
    serialized = stdout.getvalue().strip()
    report = json.loads(serialized)
    assert serialized == json.dumps(report, sort_keys=True, separators=(",", ":"))
    assert report["status"] == "passed"
    assert report["tasks"][0]["task_name"] == "Second"
    assert report["tasks"][0]["archive_entries"] == 2
    assert report["tasks"][0]["inventory"]["graph_sha256"] == "d" * 64
    assert "fixture-token" not in serialized
    assert "token=secret" not in serialized
    assert str(tmp_path) not in serialized


def test_success_does_not_repeat_structural_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    calls: list[str] = []

    result = inspect_command(
        CommandRequest(
            command=CommandName.INSPECT,
            config_path=Path("unused.toml"),
            task_names=(),
            output_format=OutputFormat.JSON,
        ),
        dependencies((task("Example"),), structure_calls=calls),
    )

    assert result.exit_code == 0
    assert calls == []


def test_inspect_human_report_isolates_task_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    configured = (task("First"), task("Second"))
    deps = dependencies(configured, fail_name="First")
    request = CommandRequest(
        command=CommandName.INSPECT,
        config_path=Path("unused.toml"),
        task_names=(),
        output_format=OutputFormat.HUMAN,
    )

    result = inspect_command(request, deps)

    assert result.exit_code == 1
    assert result.human_output == (
        "Inspection: 1 passed, 1 failed\n"
        "First: failed [source.download_failed] fixture download failed\n"
        "Second: passed; 1 profile bundle(s), 1 code node(s), graph dddddddddddd"
    )


def test_entitlement_failure_retains_download_and_structural_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    configured = (task("Unsigned"),)
    calls: list[str] = []
    result = inspect_command(
        CommandRequest(
            command=CommandName.INSPECT,
            config_path=Path("unused.toml"),
            task_names=(),
            output_format=OutputFormat.JSON,
        ),
        dependencies(configured, fail_graph=True, structure_calls=calls),
    )

    report = {key: thaw_json(value) for key, value in result.payload}
    task_reports = report["tasks"]
    assert isinstance(task_reports, list)
    task_report = task_reports[0]
    assert isinstance(task_report, dict)
    assert result.exit_code == 1
    assert task_report["source"]["downloaded_sha256"] == "a" * 64
    assert task_report["structure"]["nodes"][0]["path"] == "Payload/App.app"
    assert task_report["inventory"] is None
    assert len(calls) == 1
