"""Tests for preserve-source-suffix bundle identifier policy."""

from __future__ import annotations

import pytest

from sideloadedipa.domain import (
    BundleIdentifierMapping,
    derive_identifier_mappings,
    derive_target_bundle_id,
    validate_bundle_identifier,
)
from sideloadedipa.errors import DomainError, ErrorCode


def test_derives_root_and_descendant_targets_in_stable_order() -> None:
    mappings = derive_identifier_mappings(
        [
            "com.kdt.livecontainer.ShareExtension",
            "com.kdt.livecontainer",
            "com.kdt.livecontainer.LiveProcess",
        ],
        source_root_bundle_id="com.kdt.livecontainer",
        target_root_bundle_id="io.zeroclover.app.livecontainer",
    )

    assert mappings == (
        BundleIdentifierMapping("com.kdt.livecontainer", "io.zeroclover.app.livecontainer"),
        BundleIdentifierMapping(
            "com.kdt.livecontainer.LiveProcess",
            "io.zeroclover.app.livecontainer.LiveProcess",
        ),
        BundleIdentifierMapping(
            "com.kdt.livecontainer.ShareExtension",
            "io.zeroclover.app.livecontainer.ShareExtension",
        ),
    )


def test_explicit_target_supports_non_descendant_source() -> None:
    target = derive_target_bundle_id(
        "com.side.store.widget",
        source_root_bundle_id="com.kdt.livecontainer",
        target_root_bundle_id="io.zeroclover.app.livecontainer",
        explicit_target_bundle_id="io.zeroclover.app.livecontainer.widget",
    )

    assert target == "io.zeroclover.app.livecontainer.widget"


def test_non_descendant_requires_explicit_target() -> None:
    with pytest.raises(DomainError) as caught:
        derive_target_bundle_id(
            "com.side.store.widget",
            source_root_bundle_id="com.kdt.livecontainer",
            target_root_bundle_id="io.zeroclover.app.livecontainer",
        )

    assert caught.value.code is ErrorCode.IDENTIFIER_NON_DESCENDANT
    assert caught.value.bundle_id == "com.side.store.widget"


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ("", "source_bundle_id"),
        ("com.example.bad value", "source_root_bundle_id"),
        ("com.example.*", "target_root_bundle_id"),
        ("com.example/override", "explicit_target_bundle_id"),
    ],
)
def test_rejects_invalid_identifier_at_each_input(value: str, field: str) -> None:
    arguments = {
        "source_bundle_id": "com.example.source",
        "source_root_bundle_id": "com.example",
        "target_root_bundle_id": "io.example",
        "explicit_target_bundle_id": "io.example.source",
    }
    arguments[field] = value

    with pytest.raises(DomainError) as caught:
        derive_target_bundle_id(**arguments)

    assert caught.value.code is ErrorCode.IDENTIFIER_INVALID
    assert caught.value.safe_details == (("field", field),)


def test_validate_bundle_identifier_returns_valid_value() -> None:
    assert (
        validate_bundle_identifier("io.zeroclover.App-1", field="bundle_id")
        == "io.zeroclover.App-1"
    )


def test_rejects_case_insensitive_target_collision() -> None:
    with pytest.raises(DomainError) as caught:
        derive_identifier_mappings(
            ["com.example.One", "com.example.Two"],
            source_root_bundle_id="com.example",
            target_root_bundle_id="io.example",
            explicit_targets={
                "com.example.One": "io.example.Shared",
                "com.example.Two": "io.example.shared",
            },
        )

    assert caught.value.code is ErrorCode.IDENTIFIER_COLLISION
    assert caught.value.safe_details == (
        (
            "collisions",
            (("io.example.shared", ("com.example.One", "com.example.Two")),),
        ),
    )
