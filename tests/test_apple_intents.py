"""Tests for pure task-to-Apple-resource intent expansion."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sideloadedipa.apple.intents import derive_bundle_resource_intents
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import EntitlementMode
from sideloadedipa.errors import ConfigurationError, ErrorCode

FIXTURE = Path("tests/fixtures/configuration/signing-cases.toml")


def test_derives_livecontainer_identifiers_capabilities_and_account_names() -> None:
    task = load_configuration(FIXTURE).tasks[1]

    intents = derive_bundle_resource_intents(task)

    assert [intent.target_bundle_id for intent in intents] == [
        "io.zeroclover.app.livecontainer",
        "io.zeroclover.app.livecontainer.LaunchAppExtension",
        "io.zeroclover.app.livecontainer.LiveProcess",
        "io.zeroclover.app.livecontainer.ShareExtension",
    ]
    assert [intent.display_name for intent in intents] == [
        "LiveContainer",
        "LiveContainer LaunchAppExtension",
        "LiveContainer LiveProcess",
        "LiveContainer ShareExtension",
    ]
    assert [intent.profile_name for intent in intents] == [
        "LiveContainer Dev",
        "LiveContainer LaunchAppExtension Dev",
        "LiveContainer LiveProcess Dev",
        "LiveContainer ShareExtension Dev",
    ]
    assert all(intent.app_groups == ("group.io.zeroclover.livecontainer",) for intent in intents)
    assert intents[2].entitlement_policy.mode is EntitlementMode.PRESERVE_SOURCE


def test_production_livecontainer_derives_exact_four_resource_intents() -> None:
    task = next(
        task
        for task in load_configuration(Path("configs/tasks.toml")).tasks
        if task.task_name == "LiveContainer"
    )

    intents = derive_bundle_resource_intents(task)

    assert [intent.target_bundle_id for intent in intents] == [
        "io.zeroclover.app.livecontainer",
        "io.zeroclover.app.livecontainer.LaunchAppExtension",
        "io.zeroclover.app.livecontainer.LiveProcess",
        "io.zeroclover.app.livecontainer.ShareExtension",
    ]
    assert [intent.profile_name for intent in intents] == [
        "LiveContainer Dev",
        "LiveContainer LaunchAppExtension Dev",
        "LiveContainer LiveProcess Dev",
        "LiveContainer ShareExtension Dev",
    ]
    assert all(
        intent.app_groups == ("group.io.zeroclover.app.livecontainer",) for intent in intents
    )
    assert intents[1].required_capabilities == ("APP_GROUPS",)
    assert intents[3].required_capabilities == ("APP_GROUPS",)
    assert intents[0].required_capabilities == (
        "APP_GROUPS",
        "CLINICAL_HEALTH_RECORDS",
        "HEALTHKIT",
        "HEALTHKIT_BACKGROUND_DELIVERY",
        "INCREASED_MEMORY_LIMIT",
        "KEYCHAIN_SHARING",
    )
    assert intents[2].required_capabilities == intents[0].required_capabilities


def test_legacy_task_retains_one_root_profile_name() -> None:
    task = load_configuration(FIXTURE).tasks[0]

    assert derive_bundle_resource_intents(task)[0].profile_name == "Root Only Dev"


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ((), "exactly one root"),
        ((0, 1), "exactly one root"),
    ],
)
def test_requires_exactly_one_root_rule(rules: tuple[int, ...], message: str) -> None:
    task = load_configuration(FIXTURE).tasks[1]
    assert task.signing is not None
    bundles = tuple(
        replace(rule, role="root" if index in rules else None)
        for index, rule in enumerate(task.signing.bundles)
    )

    with pytest.raises(ConfigurationError, match=message) as caught:
        derive_bundle_resource_intents(
            replace(task, signing=replace(task.signing, bundles=bundles))
        )

    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_rejects_root_target_that_disagrees_with_task_bundle_id() -> None:
    task = load_configuration(FIXTURE).tasks[1]
    assert task.signing is not None
    root = replace(task.signing.bundles[0], target_bundle_id="io.example.wrong")

    with pytest.raises(ConfigurationError, match="root bundle rule target") as caught:
        derive_bundle_resource_intents(
            replace(task, signing=replace(task.signing, bundles=(root, *task.signing.bundles[1:])))
        )

    assert caught.value.code is ErrorCode.CONFIG_INVALID
