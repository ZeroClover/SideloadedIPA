"""Tests for the independent macOS codesign qualification oracle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from exercise_codesign_oracle import (
    signing_order,
)
from exercise_zsign_backend import (
    BackendExerciseError,
    keychain_groups,
    materialize_entitlements,
)


def profile_entitlements(bundle_identifier: str) -> dict:
    return {
        "application-identifier": f"TEAM.{bundle_identifier}",
        "com.apple.security.application-groups": ["group.example"],
        "get-task-allow": True,
        "keychain-access-groups": [f"TEAM.{bundle_identifier}", "TEAM.*"],
    }


def test_root_oracle_materializes_exact_128_authorized_groups() -> None:
    bundle_identifier = "io.zeroclover.app.livecontainer"

    result = materialize_entitlements(
        "root", bundle_identifier, profile_entitlements(bundle_identifier)
    )

    assert result["keychain-access-groups"] == keychain_groups(
        f"TEAM.{bundle_identifier}", bundle_identifier
    )
    assert len(result["keychain-access-groups"]) == 128
    assert result["keychain-access-groups"][0] == "TEAM.com.kdt.livecontainer.shared"
    assert result["keychain-access-groups"][-1].endswith(".127")


def test_extension_oracle_preserves_profile_entitlements() -> None:
    bundle_identifier = "io.zeroclover.app.livecontainer.ShareExtension"
    source = profile_entitlements(bundle_identifier)

    result = materialize_entitlements("share", bundle_identifier, source)

    assert result == source
    assert result is not source


def test_oracle_rejects_unauthorized_keychain_groups() -> None:
    bundle_identifier = "io.zeroclover.app.livecontainer.LiveProcess"
    source = profile_entitlements(bundle_identifier)
    source["keychain-access-groups"] = [f"TEAM.{bundle_identifier}"]

    with pytest.raises(BackendExerciseError, match="does not authorize 128"):
        materialize_entitlements("process", bundle_identifier, source)


def test_oracle_signing_order_is_deterministic_and_root_last() -> None:
    assert signing_order() == ["launch", "process", "share", "root"]
