"""Tests for entitlement policy materialization."""

from __future__ import annotations

import math

import pytest

from sideloadedipa.domain import (
    EntitlementContext,
    EntitlementMode,
    EntitlementPolicy,
    materialize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode


@pytest.fixture
def context() -> EntitlementContext:
    return EntitlementContext(
        team_id="TARGETTEAM",
        app_identifier_prefix="TARGETTEAM.",
        source_bundle_id="com.kdt.livecontainer",
        target_bundle_id="io.zeroclover.app.livecontainer",
        app_group_rewrites=(("group.com.kdt.shared", "group.io.zeroclover.shared"),),
    )


def test_preserve_source_applies_only_typed_team_bound_rewrites(
    context: EntitlementContext,
) -> None:
    source = {
        "application-identifier": "SOURCETEAM.com.kdt.livecontainer",
        "com.apple.developer.team-identifier": "SOURCETEAM",
        "keychain-access-groups": [
            "SOURCETEAM.com.kdt.livecontainer",
            "unrelated.shared.group",
        ],
        "com.apple.security.application-groups": ["group.com.kdt.shared"],
        "com.apple.developer.healthkit": True,
    }

    result = materialize_entitlements(
        EntitlementPolicy(EntitlementMode.PRESERVE_SOURCE), source, context
    )

    assert dict(result.values) == {
        "application-identifier": "TARGETTEAM.io.zeroclover.app.livecontainer",
        "com.apple.developer.healthkit": True,
        "com.apple.developer.team-identifier": "TARGETTEAM",
        "com.apple.security.application-groups": ("group.io.zeroclover.shared",),
        "keychain-access-groups": (
            "TARGETTEAM.com.kdt.livecontainer",
            "unrelated.shared.group",
        ),
    }
    assert [change.key for change in result.transformations] == [
        "application-identifier",
        "com.apple.developer.team-identifier",
        "keychain-access-groups",
        "com.apple.security.application-groups",
    ]
    assert result.dropped_keys == ()


def test_profile_mode_allows_explicit_drop_with_rationale(
    context: EntitlementContext,
) -> None:
    policy = EntitlementPolicy(
        EntitlementMode.PROFILE,
        allowed_drops=("com.apple.developer.unavailable",),
        drop_rationale="Not authorized for development profiles",
    )

    result = materialize_entitlements(
        policy,
        {"get-task-allow": True, "com.apple.developer.unavailable": True},
        context,
        profile_entitlements={"get-task-allow": True},
    )

    assert result.values == (("get-task-allow", True),)
    assert result.dropped_keys == ("com.apple.developer.unavailable",)


def test_template_mode_hash_is_independent_of_dictionary_order(
    context: EntitlementContext,
) -> None:
    policy = EntitlementPolicy(EntitlementMode.TEMPLATE)
    first = materialize_entitlements(
        policy,
        {},
        context,
        template_entitlements={"z": [2, 1], "a": {"two": 2, "one": 1}},
    )
    second = materialize_entitlements(
        policy,
        {},
        context,
        template_entitlements={"a": {"one": 1, "two": 2}, "z": [2, 1]},
    )

    assert first == second
    assert len(first.sha256) == 64


def test_rejects_undeclared_entitlement_drop(context: EntitlementContext) -> None:
    with pytest.raises(DomainError) as caught:
        materialize_entitlements(
            EntitlementPolicy(EntitlementMode.PROFILE),
            {"required": True},
            context,
            profile_entitlements={},
        )

    assert caught.value.code is ErrorCode.ENTITLEMENTS_UNDECLARED_DROP
    assert caught.value.safe_details == (("keys", ("required",)),)


def test_rejects_allowed_drop_without_rationale(context: EntitlementContext) -> None:
    with pytest.raises(DomainError, match="require a rationale"):
        materialize_entitlements(
            EntitlementPolicy(EntitlementMode.PROFILE, allowed_drops=("key",)),
            {},
            context,
            profile_entitlements={},
        )


@pytest.mark.parametrize(
    ("policy", "kwargs", "field"),
    [
        (EntitlementPolicy(EntitlementMode.PROFILE), {}, "profile"),
        (EntitlementPolicy(EntitlementMode.TEMPLATE), {}, "template"),
    ],
)
def test_requires_selected_policy_document(
    context: EntitlementContext,
    policy: EntitlementPolicy,
    kwargs: dict[str, object],
    field: str,
) -> None:
    with pytest.raises(DomainError) as caught:
        materialize_entitlements(policy, {}, context, **kwargs)

    assert caught.value.safe_details == (("field", field),)


def test_rejects_malformed_source_application_identifier(
    context: EntitlementContext,
) -> None:
    with pytest.raises(DomainError) as caught:
        materialize_entitlements(
            EntitlementPolicy(EntitlementMode.PRESERVE_SOURCE),
            {"application-identifier": "SOURCETEAM.other.bundle"},
            context,
        )

    assert caught.value.safe_details == (("field", "application-identifier"),)


@pytest.mark.parametrize(
    "document",
    [
        {"groups": "not-an-array"},
        {"groups": ["valid", 42]},
        {"unsupported": b"bytes"},
        {42: "non-string-key"},
        {"not-finite": math.nan},
    ],
)
def test_rejects_invalid_entitlement_value_shapes(
    context: EntitlementContext, document: dict[object, object]
) -> None:
    if "groups" in document:
        document = {"keychain-access-groups": document["groups"]}
        policy = EntitlementPolicy(EntitlementMode.PRESERVE_SOURCE)
        kwargs: dict[str, object] = {}
        source = document
    else:
        policy = EntitlementPolicy(EntitlementMode.TEMPLATE)
        kwargs = {"template_entitlements": document}
        source = {}

    with pytest.raises(DomainError):
        materialize_entitlements(policy, source, context, **kwargs)
