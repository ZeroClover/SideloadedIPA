"""Tests for typed semantic entitlement comparison."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from sideloadedipa.verification import (
    EntitlementComparisonMode,
    EntitlementIdentityContext,
    compare_entitlements,
)

IDENTITY = EntitlementIdentityContext(
    "TEAMID1234",
    "TEAMID1234.",
    "io.example.app",
)


def reasons(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    *,
    mode: EntitlementComparisonMode = EntitlementComparisonMode.EXACT,
) -> list[str]:
    return [value.reason for value in compare_entitlements(expected, actual, mode=mode).differences]


def test_exact_comparison_is_typed_and_detects_missing_and_unexpected_values() -> None:
    assert compare_entitlements({"enabled": True, "count": 1}, {"count": 1, "enabled": True}).passed
    assert reasons({"enabled": True}, {"enabled": 1}) == ["value-mismatch"]
    assert reasons({"required": True}, {"extra": True}) == ["missing", "unexpected"]


def test_only_declared_set_like_arrays_ignore_order_and_reject_duplicates() -> None:
    keychain = "keychain-access-groups"
    expected = {keychain: ["TEAMID1234.one", "TEAMID1234.two"]}

    assert compare_entitlements(expected, {keychain: list(reversed(expected[keychain]))}).passed
    assert reasons(expected, {keychain: ["TEAMID1234.one"]}) == ["set-mismatch"]
    assert reasons(expected, {keychain: ["TEAMID1234.one", "TEAMID1234.one"]}) == [
        "actual-duplicate"
    ]
    assert reasons({"ordered": [1, 2]}, {"ordered": [2, 1]}) == ["value-mismatch"]


def test_app_groups_are_exact_even_for_profile_authorization() -> None:
    key = "com.apple.security.application-groups"
    mode = EntitlementComparisonMode.PROFILE_AUTHORIZATION

    assert compare_entitlements({key: ["group.one"]}, {key: ["group.one"]}, mode=mode).passed
    assert reasons({key: ["group.one"]}, {key: ["group.one", "group.two"]}, mode=mode) == [
        "set-mismatch"
    ]


def test_profile_wildcards_are_narrow_and_signed_values_remain_exact() -> None:
    expected = {
        "application-identifier": "TEAMID1234.io.example.app",
        "keychain-access-groups": ["TEAMID1234.io.example.app", "TEAMID1234.shared"],
    }
    allowed = {
        "application-identifier": "TEAMID1234.*",
        "keychain-access-groups": ["TEAMID1234.*"],
    }

    assert compare_entitlements(
        expected,
        allowed,
        mode=EntitlementComparisonMode.PROFILE_AUTHORIZATION,
    ).passed
    assert not compare_entitlements(expected, allowed).passed
    assert reasons(
        {"com.apple.developer.healthkit": "value"},
        {"com.apple.developer.healthkit": "*"},
        mode=EntitlementComparisonMode.PROFILE_AUTHORIZATION,
    ) == ["value-mismatch"]


@pytest.mark.parametrize(
    ("actual", "reason"),
    [
        ({"application-identifier": "OTHER.io.example.app"}, "target-identifier-mismatch"),
        (
            {
                "application-identifier": "TEAMID1234.io.example.app",
                "com.apple.developer.team-identifier": "OTHER",
            },
            "team-mismatch",
        ),
        (
            {
                "application-identifier": "TEAMID1234.io.example.app",
                "keychain-access-groups": ["OTHER.shared"],
            },
            "team-prefix-mismatch",
        ),
    ],
)
def test_exact_comparison_rejects_wrong_team_bound_values(
    actual: dict[str, object], reason: str
) -> None:
    expected = dict(actual)

    comparison = compare_entitlements(expected, actual, identity=IDENTITY)

    assert reason in [value.reason for value in comparison.differences]


def test_nested_dictionaries_use_exact_or_authorization_key_semantics() -> None:
    expected = {"nested": {"required": True}}
    actual = {"nested": {"required": True, "default": False}}

    assert not compare_entitlements(expected, actual).passed
    assert compare_entitlements(
        expected,
        actual,
        mode=EntitlementComparisonMode.PROFILE_AUTHORIZATION,
    ).passed
