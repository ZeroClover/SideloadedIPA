"""Tests for typed legacy-compatible task configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from sideloadedipa.config import load_configuration, parse_configuration
from sideloadedipa.domain import R2Config, SourceKind
from sideloadedipa.errors import ConfigurationError, ErrorCode


def direct_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "task_name": "Direct",
        "app_name": "Direct App",
        "bundle_id": "com.example.direct",
        "ipa_url": "https://example.com/Direct.ipa",
    }
    task.update(overrides)
    return task


def test_loads_current_production_configuration() -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))

    assert [task.task_name for task in configuration.tasks] == [
        "JHenTai",
        "Eros FE",
        "Asspp",
        "PiliPlus",
        "StikDebug",
    ]
    assert configuration.tasks[0].bundle_id == "io.zeroclover.app.jhentai"
    assert configuration.tasks[0].source.kind is SourceKind.GITHUB_RELEASE
    assert configuration.tasks[0].source.release_glob == "*.ipa"
    assert configuration.tasks[-1].icon_path == "ipa:"
    assert configuration.r2 == R2Config()


def test_preserves_legacy_defaults_and_r2_field_names() -> None:
    configuration = parse_configuration(
        {
            "r2": {"key_prefix": "/signed/apps/", "apps_json_key": "registry/apps.json"},
            "tasks": [direct_task(app_name="Legacy Name")],
        }
    )

    task = configuration.tasks[0]
    assert task.slug == "Legacy_Name"
    assert task.source.kind is SourceKind.DIRECT_URL
    assert task.source.release_glob is None
    assert configuration.r2 == R2Config(ipa_prefix="signed/apps", registry_key="registry/apps.json")


def test_parses_existing_repository_source_options_and_icons() -> None:
    configuration = parse_configuration(
        {
            "tasks": [
                {
                    "task_name": "Repo",
                    "app_name": "Repo App",
                    "bundle_id": "com.example.repo-app",
                    "repo_url": "git@github.com:example/repo.git",
                    "release_glob": "Repo-*.ipa",
                    "use_prerelease": True,
                    "slug": "repo.app",
                    "icon_path": "assets/Icon.png",
                },
                direct_task(icon_path="https://example.com/icon.png"),
                direct_task(task_name="IPA Icon", icon_path="ipa:"),
            ]
        }
    )

    repository = configuration.tasks[0]
    assert repository.source.release_glob == "Repo-*.ipa"
    assert repository.source.use_prerelease is True
    assert repository.icon_path == "assets/Icon.png"


@pytest.mark.parametrize(
    ("task", "field"),
    [
        ({}, "task_name"),
        (direct_task(app_name=""), "app_name"),
        (direct_task(bundle_id="bad bundle"), "bundle_id"),
        (direct_task(repo_url="https://github.com/example/repo"), "ipa_url|repo_url"),
        (direct_task(ipa_url=None), "ipa_url|repo_url"),
        (direct_task(ipa_url="ftp://example.com/App.ipa"), "ipa_url"),
        (
            direct_task(ipa_url=None, repo_url="https://gitlab.com/example/repo"),
            "repo_url",
        ),
        (direct_task(slug="bad/slug"), "slug"),
        (direct_task(icon_path="assets/Icon.png"), "icon_path"),
        (direct_task(use_prerelease="yes"), "use_prerelease"),
        (direct_task(release_glob="*.ipa"), "release_glob|use_prerelease"),
    ],
)
def test_rejects_invalid_legacy_task_fields(task: dict[str, object], field: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration({"tasks": [task]})

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert ("field", field) in caught.value.safe_details


@pytest.mark.parametrize("tasks", [None, {}, [], "task"])
def test_requires_non_empty_task_array(tasks: object) -> None:
    with pytest.raises(ConfigurationError, match="non-empty array"):
        parse_configuration({"tasks": tasks})


def test_requires_each_task_to_be_a_table() -> None:
    with pytest.raises(ConfigurationError, match=r"tasks\[0\] must be a table"):
        parse_configuration({"tasks": ["not-a-table"]})


def test_rejects_non_string_optional_field() -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration({"tasks": [direct_task(icon_path=42)]})

    assert caught.value.safe_details == (("field", "icon_path"),)


def test_reports_missing_and_malformed_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ConfigurationError) as missing_error:
        load_configuration(missing)
    assert missing_error.value.code is ErrorCode.CONFIG_MISSING
    assert missing_error.value.safe_details == (("path", "missing.toml"),)

    malformed = tmp_path / "malformed.toml"
    malformed.write_text("tasks = [", encoding="utf-8")
    with pytest.raises(ConfigurationError) as malformed_error:
        load_configuration(malformed)
    assert malformed_error.value.code is ErrorCode.CONFIG_INVALID
