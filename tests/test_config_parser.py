"""Tests for typed legacy-compatible task configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from sideloadedipa.apple.intents import derive_bundle_resource_intents
from sideloadedipa.config import load_configuration, parse_configuration
from sideloadedipa.domain import (
    BatchPublicationPolicy,
    EntitlementMode,
    ProfileType,
    PublicationConfig,
    R2Config,
    SourceKind,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.pipeline.sign_stage import policy_sha256


def direct_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "task_name": "Direct",
        "app_name": "Direct App",
        "bundle_id": "com.example.direct",
        "ipa_url": "https://example.com/Direct.ipa",
        "ipa_sha256": "a" * 64,
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
        "LiveContainer",
        "Reynard",
        "StikDebug",
    ]
    assert configuration.tasks[0].bundle_id == "io.zeroclover.app.jhentai"
    assert configuration.tasks[0].source.kind is SourceKind.GITHUB_RELEASE
    assert configuration.tasks[0].source.release_glob == "*.ipa"
    assert configuration.tasks[-1].icon_path == "ipa:"
    assert configuration.r2 == R2Config()
    assert configuration.publication == PublicationConfig()
    assert all(task.publication_enabled for task in configuration.tasks)
    assert {
        task.task_name: policy_sha256(task)
        for task in configuration.tasks
        if task.signing is not None
    } == {
        "LiveContainer": "ab8417518dd41fe9bc8c026331827dc96287b9a3f9f5df2cda930fb8c1328237",
        "Reynard": "413b9b8d80376d7025aa9b0221b6209b96afa966b28be76da804934a89365d93",
    }


def test_every_production_task_declares_publication_enabled_explicitly() -> None:
    import tomllib

    document = tomllib.loads(Path("configs/tasks.toml").read_text())

    assert document["tasks"]
    assert all("publication_enabled" in task for task in document["tasks"])


def test_production_livecontainer_is_exactly_scoped_and_publishing() -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))
    task = next(task for task in configuration.tasks if task.task_name == "LiveContainer")

    assert task.bundle_id == "io.zeroclover.app.livecontainer"
    assert task.source.release_glob == "LiveContainer.ipa"
    assert task.icon_path == "Resources/Assets.xcassets/AppIcon.appiconset/AppIcon1024.png"
    assert task.publication_enabled is True
    assert task.signing is not None
    assert task.signing.app_groups == (("shared", "group.io.zeroclover.app.livecontainer"),)
    assert task.signing.manual_app_group_associations == ("group.io.zeroclover.app.livecontainer",)
    assert [rule.source_bundle_id for rule in task.signing.bundles] == [
        "com.kdt.livecontainer",
        "com.kdt.livecontainer.LiveProcess",
        "com.kdt.livecontainer.LaunchAppExtension",
        "com.kdt.livecontainer.ShareExtension",
    ]
    assert task.signing.bundles[0].target_bundle_id == task.bundle_id
    assert task.signing.bundles[0].role == "root"
    assert all(
        str(rule.entitlement_policy.template_path)
        == "configs/signing/livecontainer/root-process.plist"
        for rule in task.signing.bundles[:2]
    )
    assert all(
        rule.entitlement_policy.mode is EntitlementMode.PROFILE for rule in task.signing.bundles[2:]
    )


def test_production_reynard_is_exactly_scoped_and_publishing() -> None:
    configuration = load_configuration(Path("configs/tasks.toml"))
    task = next(task for task in configuration.tasks if task.task_name == "Reynard")

    assert task.app_name == "Reynard Browser"
    assert task.bundle_id == "io.zeroclover.app.reynard"
    assert task.source.release_glob == "Reynard.ipa"
    assert task.icon_path == "browser/Reynard/Resources/Assets.xcassets/AppIcon.appiconset/icon.png"
    assert task.publication_enabled is True
    assert task.signing is not None
    assert task.signing.app_groups == ()
    assert task.signing.manual_app_group_associations == ()
    assert [rule.source_bundle_id for rule in task.signing.bundles] == [
        "com.minh-ton.Reynard",
        "com.minh-ton.Reynard.Helper",
        "com.minh-ton.Reynard.OpenIn",
    ]
    assert task.signing.bundles[0].target_bundle_id == task.bundle_id
    assert task.signing.bundles[0].role == "root"
    assert task.signing.bundles[0].required_capabilities == ("INCREASED_MEMORY_LIMIT",)
    assert all(
        rule.entitlement_policy.mode is EntitlementMode.PROFILE for rule in task.signing.bundles
    )
    intents = {intent.source_bundle_id: intent for intent in derive_bundle_resource_intents(task)}
    assert {
        source_bundle_id: intent.target_bundle_id for source_bundle_id, intent in intents.items()
    } == {
        "com.minh-ton.Reynard": "io.zeroclover.app.reynard",
        "com.minh-ton.Reynard.Helper": "io.zeroclover.app.reynard.Helper",
        "com.minh-ton.Reynard.OpenIn": "io.zeroclover.app.reynard.OpenIn",
    }
    assert intents["com.minh-ton.Reynard"].required_capabilities == ("INCREASED_MEMORY_LIMIT",)


def test_defaults_new_tasks_to_non_publishing_and_preserves_r2_field_names() -> None:
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
    assert task.source.ipa_sha256 == "a" * 64
    assert task.publication_enabled is False
    assert configuration.r2 == R2Config(ipa_prefix="signed/apps", registry_key="registry/apps.json")


def test_parses_batch_publication_policy() -> None:
    configuration = parse_configuration(
        {
            "publication": {"batch_policy": "independent"},
            "tasks": [direct_task()],
        }
    )

    assert configuration.publication.batch_policy is BatchPublicationPolicy.INDEPENDENT


def test_rejects_unknown_batch_publication_policy() -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration(
            {
                "publication": {"batch_policy": "partial"},
                "tasks": [direct_task()],
            }
        )

    assert "batch_policy" in str(caught.value)


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


def test_parses_multi_bundle_signing_schema() -> None:
    task = direct_task(
        bundle_id="io.zeroclover.app.livecontainer",
        signing={
            "app_groups": {
                "shared": "group.io.zeroclover.livecontainer",
                "secondary_group": "group.io.zeroclover.secondary",
            },
            "manual_app_group_associations": ["shared"],
            "bundles": [
                {
                    "source_bundle_id": "com.kdt.livecontainer",
                    "role": "root",
                    "target_bundle_id": "io.zeroclover.app.livecontainer",
                    "required_capabilities": ["APP_GROUPS", "HEALTHKIT"],
                    "entitlement_mode": "template",
                    "entitlements_file": "configs/signing/livecontainer/root.plist",
                    "allowed_entitlement_drops": ["com.apple.developer.example"],
                    "drop_rationale": "Unavailable for development signing",
                },
                {
                    "source_bundle_id": "com.kdt.livecontainer.ShareExtension",
                    "entitlement_mode": "preserve-source",
                },
            ],
        },
    )

    parsed_task = parse_configuration({"tasks": [task]}).tasks[0]
    signing = parsed_task.signing

    assert signing is not None
    assert signing.app_groups == (
        ("secondary_group", "group.io.zeroclover.secondary"),
        ("shared", "group.io.zeroclover.livecontainer"),
    )
    assert signing.manual_app_group_associations == ("group.io.zeroclover.livecontainer",)
    root = signing.bundles[0]
    assert root.required_capabilities == ("APP_GROUPS", "HEALTHKIT")
    assert root.entitlement_policy.mode is EntitlementMode.TEMPLATE
    assert str(root.entitlement_policy.template_path) == "configs/signing/livecontainer/root.plist"
    assert root.entitlement_policy.allowed_drops == ("com.apple.developer.example",)
    intents = derive_bundle_resource_intents(parsed_task)
    assert {intent.profile_type for intent in intents} == {ProfileType.IOS_APP_DEVELOPMENT}
    assert {intent.source_bundle_id: intent.target_bundle_id for intent in intents} == {
        "com.kdt.livecontainer": "io.zeroclover.app.livecontainer",
        "com.kdt.livecontainer.ShareExtension": ("io.zeroclover.app.livecontainer.ShareExtension"),
    }


def test_signing_schema_exposes_only_operator_choices() -> None:
    signing = parse_configuration({"tasks": [direct_task(signing={})]}).tasks[0].signing

    assert signing is not None
    assert signing.app_groups == ()
    assert signing.manual_app_group_associations == ()
    assert signing.bundles == ()
    assert not any(
        hasattr(signing, field)
        for field in ("id_strategy", "unknown_profile_bundles", "profile_type")
    )


@pytest.mark.parametrize(
    ("field", "value", "fixed_behavior"),
    [
        (
            "id_strategy",
            "preserve-source-suffix",
            "preserve-source-suffix identifier mapping is now a fixed package invariant",
        ),
        (
            "unknown_profile_bundles",
            "error",
            "uncovered profile-bearing bundles are now always rejected",
        ),
        (
            "profile_type",
            "IOS_APP_DEVELOPMENT",
            "package profiles now always use IOS_APP_DEVELOPMENT",
        ),
    ],
)
def test_rejects_removed_signing_fields_with_precise_migration_guidance(
    field: str,
    value: object,
    fixed_behavior: str,
) -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration({"tasks": [direct_task(signing={field: value})]})

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.message == f"{field} has been removed because {fixed_behavior}"
    assert caught.value.safe_details == (("field", field),)
    assert caught.value.remediation == f"remove signing.{field}; {fixed_behavior}"


def test_parses_per_task_publication_gate() -> None:
    task = parse_configuration({"tasks": [direct_task(publication_enabled=False)]}).tasks[0]

    assert task.publication_enabled is False


def test_normalizes_direct_source_sha256_to_canonical_lowercase() -> None:
    task = parse_configuration({"tasks": [direct_task(ipa_sha256="A" * 64)]}).tasks[0]

    assert task.source.ipa_sha256 == "a" * 64


@pytest.mark.parametrize("ipa_sha256", [None, "short", f"sha256:{'a' * 64}", "g" * 64])
def test_direct_source_requires_canonical_sha256_with_migration_command(
    ipa_sha256: str | None,
) -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration({"tasks": [direct_task(ipa_sha256=ipa_sha256)]})

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert caught.value.safe_details == (("field", "ipa_sha256"),)
    assert caught.value.remediation is not None
    assert "shasum -a 256" in caught.value.remediation


def test_github_source_rejects_direct_source_digest() -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration(
            {
                "tasks": [
                    direct_task(
                        ipa_url=None,
                        repo_url="https://github.com/example/repo",
                    )
                ]
            }
        )

    assert caught.value.safe_details == (("field", "ipa_sha256"),)


@pytest.mark.parametrize(
    ("task", "field"),
    [
        ({}, "task_name"),
        (direct_task(app_name=""), "app_name"),
        (direct_task(bundle_id="bad bundle"), "bundle_id"),
        (direct_task(repo_url="https://github.com/example/repo"), "ipa_url|repo_url"),
        (direct_task(ipa_url=None), "ipa_url|repo_url"),
        (direct_task(ipa_url="http://example.com/App.ipa"), "ipa_url"),
        (direct_task(ipa_url="ftp://example.com/App.ipa"), "ipa_url"),
        (
            direct_task(
                ipa_url=None,
                ipa_sha256=None,
                repo_url="https://gitlab.com/example/repo",
            ),
            "repo_url",
        ),
        (direct_task(slug="bad/slug"), "slug"),
        (direct_task(icon_path="assets/Icon.png"), "icon_path"),
        (direct_task(use_prerelease="yes"), "use_prerelease"),
        (direct_task(release_glob="*.ipa"), "release_glob|use_prerelease"),
        (direct_task(publication_enabled="false"), "publication_enabled"),
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


@pytest.mark.parametrize(
    ("signing", "field"),
    [
        ("invalid", "signing"),
        ({"app_groups": []}, "signing.app_groups"),
        ({"app_groups": {"bad alias": "group.example"}}, "signing.app_groups"),
        ({"app_groups": {"shared": 42}}, "signing.app_groups.shared"),
        ({"manual_app_group_associations": ["missing"]}, "manual_app_group_associations"),
        ({"bundles": {}}, "signing.bundles"),
        ({"bundles": ["invalid"]}, "signing.bundles[0]"),
        ({"bundles": [{}]}, "source_bundle_id"),
        (
            {"bundles": [{"source_bundle_id": "com.example", "entitlement_mode": "unknown"}]},
            "entitlement_mode",
        ),
        (
            {"bundles": [{"source_bundle_id": "com.example", "entitlement_mode": "template"}]},
            "entitlements_file",
        ),
        (
            {"bundles": [{"source_bundle_id": "com.example", "entitlements_file": "file.plist"}]},
            "entitlements_file",
        ),
        (
            {
                "bundles": [
                    {"source_bundle_id": "com.example", "required_capabilities": "APP_GROUPS"}
                ]
            },
            "required_capabilities",
        ),
        (
            {
                "bundles": [
                    {
                        "source_bundle_id": "com.example",
                        "allowed_entitlement_drops": ["key"],
                    }
                ]
            },
            "drop_rationale",
        ),
    ],
)
def test_rejects_invalid_signing_schema(signing: object, field: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        parse_configuration({"tasks": [direct_task(signing=signing)]})

    assert ("field", field) in caught.value.safe_details


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


def test_example_configuration_parses_through_the_production_loader() -> None:
    configuration = load_configuration(Path("configs/tasks.toml.example"))

    assert configuration.tasks
    livecontainer = next(task for task in configuration.tasks if task.task_name == "LiveContainer")
    assert policy_sha256(livecontainer) == (
        "1dc7d2fffb3a46f8466d192b1fbe0f62c9d8ab52e1410777b005cf0fb4f9c66b"
    )
