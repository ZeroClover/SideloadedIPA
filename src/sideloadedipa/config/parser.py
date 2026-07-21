"""Parse the legacy task file into immutable domain values."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import NoReturn, TypeVar
from urllib.parse import urlsplit

from sideloadedipa.domain import (
    BatchPublicationPolicy,
    BundleRule,
    EntitlementMode,
    EntitlementPolicy,
    IdentifierStrategy,
    ProfileType,
    PublicationConfig,
    R2Config,
    SigningPolicy,
    SourceConfig,
    SourceKind,
    Task,
    TaskConfiguration,
    UnknownProfileBundlePolicy,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode

_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
_GITHUB_REPOSITORY_PATTERN = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$"
)
_ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_EnumValue = TypeVar("_EnumValue", bound=StrEnum)


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


def _string_tuple(value: object, field: str, task_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        _fail(f"{field} must be an array of non-empty strings", field, task_name)
    return tuple(item.strip() for item in value)


def _enum_value(
    enum_type: type[_EnumValue],
    value: object,
    field: str,
    task_name: str | None,
) -> _EnumValue:
    if not isinstance(value, str):
        _fail(f"{field} must be a string", field, task_name)
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        _fail(f"{field} must be one of: {allowed}", field, task_name)


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


def _parse_entitlement_policy(raw: Mapping[str, object], task_name: str) -> EntitlementPolicy:
    mode = _enum_value(
        EntitlementMode,
        raw.get("entitlement_mode", EntitlementMode.PROFILE.value),
        "entitlement_mode",
        task_name,
    )
    file_value = _optional_string(raw, "entitlements_file", task_name=task_name)
    if mode is EntitlementMode.TEMPLATE and file_value is None:
        _fail(
            "template entitlement mode requires entitlements_file",
            "entitlements_file",
            task_name,
        )
    if mode is not EntitlementMode.TEMPLATE and file_value is not None:
        _fail(
            "entitlements_file is valid only for template entitlement mode",
            "entitlements_file",
            task_name,
        )

    allowed_drops = _string_tuple(
        raw.get("allowed_entitlement_drops"), "allowed_entitlement_drops", task_name
    )
    rationale = _optional_string(raw, "drop_rationale", task_name=task_name)
    if allowed_drops and rationale is None:
        _fail(
            "allowed_entitlement_drops requires drop_rationale",
            "drop_rationale",
            task_name,
        )
    return EntitlementPolicy(
        mode=mode,
        template_path=PurePosixPath(file_value) if file_value is not None else None,
        allowed_drops=allowed_drops,
        drop_rationale=rationale,
    )


def _parse_bundle_rule(value: object, index: int, task_name: str) -> BundleRule:
    raw = _mapping(value, f"signing.bundles[{index}]")
    source_bundle_id = _string(raw, "source_bundle_id", task_name=task_name)
    return BundleRule(
        source_bundle_id=source_bundle_id,
        target_bundle_id=_optional_string(raw, "target_bundle_id", task_name=task_name),
        role=_optional_string(raw, "role", task_name=task_name),
        required_capabilities=_string_tuple(
            raw.get("required_capabilities"), "required_capabilities", task_name
        ),
        entitlement_policy=_parse_entitlement_policy(raw, task_name),
    )


def _parse_signing(value: object, task_name: str) -> SigningPolicy:
    raw = _mapping(value, "signing")
    app_groups_raw = _mapping(raw.get("app_groups", {}), "signing.app_groups")
    app_groups: list[tuple[str, str]] = []
    for alias, identifier in app_groups_raw.items():
        if not _ALIAS_PATTERN.fullmatch(alias):
            _fail("App Group alias has invalid syntax", "signing.app_groups", task_name)
        if not isinstance(identifier, str) or not identifier.strip():
            _fail(
                "App Group identifiers must be non-empty strings",
                f"signing.app_groups.{alias}",
                task_name,
            )
        app_groups.append((alias, identifier.strip()))

    bundles_raw = raw.get("bundles", [])
    if not isinstance(bundles_raw, list):
        _fail("signing.bundles must be an array of tables", "signing.bundles", task_name)

    return SigningPolicy(
        id_strategy=_enum_value(
            IdentifierStrategy,
            raw.get("id_strategy", IdentifierStrategy.PRESERVE_SOURCE_SUFFIX.value),
            "id_strategy",
            task_name,
        ),
        unknown_profile_bundles=_enum_value(
            UnknownProfileBundlePolicy,
            raw.get("unknown_profile_bundles", UnknownProfileBundlePolicy.ERROR.value),
            "unknown_profile_bundles",
            task_name,
        ),
        profile_type=_enum_value(
            ProfileType,
            raw.get("profile_type", ProfileType.IOS_APP_DEVELOPMENT.value),
            "profile_type",
            task_name,
        ),
        app_groups=tuple(sorted(app_groups)),
        bundles=tuple(
            _parse_bundle_rule(bundle, index, task_name) for index, bundle in enumerate(bundles_raw)
        ),
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

    publication_enabled = raw.get("publication_enabled", True)
    if not isinstance(publication_enabled, bool):
        _fail("publication_enabled must be a boolean", "publication_enabled", task_name)

    return Task(
        task_name=task_name,
        app_name=app_name,
        bundle_id=bundle_id,
        source=source,
        slug=slug,
        icon_path=icon_path,
        signing=(_parse_signing(raw["signing"], task_name) if "signing" in raw else None),
        publication_enabled=publication_enabled,
    )


def _parse_r2(value: object) -> R2Config:
    raw = _mapping(value, "r2")
    prefix = _string(raw, "key_prefix", default="apps").strip("/") or "apps"
    registry_key = _string(raw, "apps_json_key", default="site/apps.json")
    return R2Config(ipa_prefix=prefix, registry_key=registry_key)


def _parse_publication(value: object) -> PublicationConfig:
    raw = _mapping(value, "publication")
    return PublicationConfig(
        batch_policy=_enum_value(
            BatchPublicationPolicy,
            raw.get("batch_policy", BatchPublicationPolicy.ATOMIC.value),
            "batch_policy",
            None,
        )
    )


def parse_configuration(document: Mapping[str, object]) -> TaskConfiguration:
    """Validate a decoded TOML document without performing side effects."""

    raw_tasks = document.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        _fail("tasks must be a non-empty array of tables", "tasks")
    tasks = tuple(_parse_task(value, index) for index, value in enumerate(raw_tasks))
    r2 = _parse_r2(document.get("r2", {}))
    publication = _parse_publication(document.get("publication", {}))
    return TaskConfiguration(tasks=tasks, r2=r2, publication=publication)


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
