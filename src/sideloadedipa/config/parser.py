"""Parse the legacy task file into immutable domain values."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from sideloadedipa.domain import R2Config, SourceConfig, SourceKind, Task, TaskConfiguration
from sideloadedipa.errors import ConfigurationError, ErrorCode

_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
_GITHUB_REPOSITORY_PATTERN = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$"
)


def _fail(message: str, field: str, task_name: str | None = None) -> NoReturn:
    raise ConfigurationError(
        ErrorCode.CONFIG_INVALID,
        message,
        task_name=task_name,
        safe_details=(("field", field),),
    )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be a table", field)
    return value


def _string(
    mapping: Mapping[str, object],
    field: str,
    *,
    task_name: str | None = None,
    default: str | None = None,
) -> str:
    value = mapping.get(field, default)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} must be a non-empty string", field, task_name)
    return value.strip()


def _optional_string(
    mapping: Mapping[str, object], field: str, *, task_name: str | None = None
) -> str | None:
    value = mapping.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} must be a non-empty string", field, task_name)
    return value.strip()


def _http_url(value: str, field: str, task_name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _fail(f"{field} must be an HTTP or HTTPS URL", field, task_name)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return re.sub(r"_+", "_", slug).strip("._-") or "app"


def _parse_source(raw: Mapping[str, object], task_name: str) -> SourceConfig:
    ipa_url = _optional_string(raw, "ipa_url", task_name=task_name)
    repo_url = _optional_string(raw, "repo_url", task_name=task_name)
    if (ipa_url is None) == (repo_url is None):
        _fail(
            "exactly one of ipa_url or repo_url must be configured",
            "ipa_url|repo_url",
            task_name,
        )

    release_glob = _optional_string(raw, "release_glob", task_name=task_name)
    use_prerelease = raw.get("use_prerelease", False)
    if not isinstance(use_prerelease, bool):
        _fail("use_prerelease must be a boolean", "use_prerelease", task_name)

    if ipa_url is not None:
        _http_url(ipa_url, "ipa_url", task_name)
        if release_glob is not None or use_prerelease:
            _fail(
                "release_glob and use_prerelease require repo_url",
                "release_glob|use_prerelease",
                task_name,
            )
        return SourceConfig(kind=SourceKind.DIRECT_URL, location=ipa_url)

    assert repo_url is not None
    if not _GITHUB_REPOSITORY_PATTERN.fullmatch(repo_url):
        _fail("repo_url must identify a GitHub repository", "repo_url", task_name)
    return SourceConfig(
        kind=SourceKind.GITHUB_RELEASE,
        location=repo_url,
        release_glob=release_glob or "*.ipa",
        use_prerelease=use_prerelease,
    )


def _parse_task(value: object, index: int) -> Task:
    raw = _mapping(value, f"tasks[{index}]")
    task_name = _string(raw, "task_name")
    app_name = _string(raw, "app_name", task_name=task_name)
    bundle_id = _string(raw, "bundle_id", task_name=task_name)
    if not _BUNDLE_ID_PATTERN.fullmatch(bundle_id):
        _fail(
            "bundle_id may contain only letters, numbers, hyphens, and periods",
            "bundle_id",
            task_name,
        )

    source = _parse_source(raw, task_name)
    slug = _optional_string(raw, "slug", task_name=task_name) or _slugify(app_name)
    if not _SLUG_PATTERN.fullmatch(slug):
        _fail(
            "slug may contain only letters, numbers, dots, underscores, and hyphens",
            "slug",
            task_name,
        )

    icon_path = _optional_string(raw, "icon_path", task_name=task_name)
    if icon_path is not None:
        is_url = urlsplit(icon_path).scheme in {"http", "https"}
        if is_url:
            _http_url(icon_path, "icon_path", task_name)
        elif icon_path != "ipa:" and source.kind is not SourceKind.GITHUB_RELEASE:
            _fail("a repository-relative icon_path requires repo_url", "icon_path", task_name)

    return Task(
        task_name=task_name,
        app_name=app_name,
        bundle_id=bundle_id,
        source=source,
        slug=slug,
        icon_path=icon_path,
    )


def _parse_r2(value: object) -> R2Config:
    raw = _mapping(value, "r2")
    prefix = _string(raw, "key_prefix", default="apps").strip("/") or "apps"
    registry_key = _string(raw, "apps_json_key", default="site/apps.json")
    return R2Config(ipa_prefix=prefix, registry_key=registry_key)


def parse_configuration(document: Mapping[str, object]) -> TaskConfiguration:
    """Validate a decoded TOML document without performing side effects."""

    raw_tasks = document.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        _fail("tasks must be a non-empty array of tables", "tasks")
    tasks = tuple(_parse_task(value, index) for index, value in enumerate(raw_tasks))
    r2 = _parse_r2(document.get("r2", {}))
    return TaskConfiguration(tasks=tasks, r2=r2)


def load_configuration(path: Path) -> TaskConfiguration:
    """Load and validate a task TOML file."""

    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_MISSING,
            "configuration file does not exist",
            remediation="provide an existing task configuration path",
            safe_details=(("path", path.name),),
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            ErrorCode.CONFIG_INVALID,
            "configuration file could not be decoded",
            safe_details=(("path", path.name),),
        ) from error
    return parse_configuration(document)
