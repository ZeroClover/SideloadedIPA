"""Tests for profile requests built by the concrete Apple command backend."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sideloadedipa.apple_commands as commands
from sideloadedipa.adapters.apple import ProfileReconciliationResult, ProfileSyncRequest
from sideloadedipa.apple_commands import AscAppleCommandBackend
from sideloadedipa.apple_intents import BundleResourceIntent
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleDeviceState,
    AppleStateSnapshot,
    CertificateIdentity,
    EntitlementMode,
    EntitlementPolicy,
    ProfileType,
    ProvisioningProfile,
    SourceConfig,
    SourceKind,
    Task,
    thaw_json,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.profile_storage import profile_relative_path

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def task() -> Task:
    return Task(
        task_name="Example",
        app_name="Example",
        bundle_id="io.example.app",
        source=SourceConfig(SourceKind.DIRECT_URL, "https://example.com/App.ipa"),
        slug="Example",
    )


def intent(mode: EntitlementMode = EntitlementMode.PROFILE) -> BundleResourceIntent:
    return BundleResourceIntent(
        task_name="Example",
        display_name="Example",
        profile_name="Example Dev",
        source_bundle_id="com.upstream.app",
        target_bundle_id="io.example.app",
        profile_type=ProfileType.IOS_APP_DEVELOPMENT,
        required_capabilities=(
            "APP_GROUPS",
            "CLINICAL_HEALTH_RECORDS",
            "HEALTHKIT",
            "HEALTHKIT_BACKGROUND_DELIVERY",
            "INCREASED_MEMORY_LIMIT",
            "KEYCHAIN_SHARING",
        ),
        app_groups=("group.io.example.shared",),
        entitlement_policy=EntitlementPolicy(mode),
    )


def certificate() -> CertificateIdentity:
    return CertificateIdentity(
        resource_id="CERT_ONE",
        team_id="TEAMID1234",
        serial_number="1234ABCD",
        public_key_sha256="a" * 64,
        certificate_sha256="b" * 64,
        expires_at=NOW + timedelta(days=90),
    )


def bundle(seed_id: str | None = "PREFIX9876") -> AppleBundleIdentifierState:
    return AppleBundleIdentifierState(
        resource_id="BUNDLE_ONE",
        identifier="io.example.app",
        name="Example",
        platform="IOS",
        seed_id=seed_id,
    )


def device(
    resource_id: str, udid_sha256: str, *, status: str = "ENABLED", device_class: str = "IPHONE"
) -> AppleDeviceState:
    return AppleDeviceState(resource_id, "Device", "IOS", status, device_class, udid_sha256)


def snapshot(*devices: AppleDeviceState) -> AppleStateSnapshot:
    return AppleStateSnapshot("snapshot", (bundle(),), (), (), tuple(devices), ())


def backend() -> AscAppleCommandBackend:
    value = object.__new__(AscAppleCommandBackend)
    value.profiles = object()
    return value


def test_builds_exact_profile_request_from_public_apple_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, ProfileSyncRequest] = {}
    content = b"validated profile"
    expected_result = ProfileReconciliationResult(
        ProvisioningProfile(
            resource_id="PROFILE_ONE",
            name="Example Dev",
            profile_type=ProfileType.IOS_APP_DEVELOPMENT,
            bundle_id="io.example.app",
            application_identifier="PREFIX9876.io.example.app",
            team_id="TEAMID1234",
            certificate_sha256="b" * 64,
            device_ids=("hash-a", "hash-b"),
            created_at=NOW,
            expires_at=NOW + timedelta(days=90),
            profile_sha256=hashlib.sha256(content).hexdigest(),
            path=profile_relative_path("Example", "io.example.app"),
            entitlements=(),
        ),
        content,
    )

    class RecordingReconciler:
        def __init__(self, gateway: object, validator: object) -> None:
            assert gateway is concrete.profiles

        def ensure(self, request: ProfileSyncRequest) -> ProfileReconciliationResult:
            captured["request"] = request
            return expected_result

    concrete = backend()
    monkeypatch.setattr(commands, "ProfileReconciler", RecordingReconciler)

    result = concrete.ensure_profile(
        task=task(),
        intent=intent(),
        snapshot=snapshot(
            device("DEVICE_B", "hash-b"),
            device("DEVICE_A", "hash-a"),
            device("DISABLED", "hash-disabled", status="DISABLED"),
            device("MAC", "hash-mac", device_class="MAC"),
        ),
        certificate=certificate(),
        bundle=bundle(),
        config_path=Path("configs/tasks.toml"),
    )

    request = captured["request"]
    entitlements = {
        key: thaw_json(value) for key, value in request.validation.expected_entitlements
    }
    assert result is expected_result
    assert request.device_resource_ids == ("DEVICE_A", "DEVICE_B")
    assert request.validation.device_udid_sha256 == ("hash-a", "hash-b")
    assert request.validation.application_identifier == "PREFIX9876.io.example.app"
    assert request.validation.team_id == "TEAMID1234"
    assert entitlements == {
        "application-identifier": "PREFIX9876.io.example.app",
        "com.apple.developer.healthkit": True,
        "com.apple.developer.healthkit.access": ["health-records"],
        "com.apple.developer.healthkit.background-delivery": True,
        "com.apple.developer.kernel.increased-memory-limit": True,
        "com.apple.developer.team-identifier": "TEAMID1234",
        "com.apple.security.application-groups": ["group.io.example.shared"],
        "get-task-allow": True,
        "keychain-access-groups": ["PREFIX9876.io.example.app"],
    }


def test_profile_request_fails_closed_without_prefix_devices_or_materialized_source() -> None:
    concrete = backend()

    with pytest.raises(DomainError) as missing_prefix:
        concrete.ensure_profile(
            task=task(),
            intent=intent(),
            snapshot=snapshot(device("DEVICE", "hash")),
            certificate=certificate(),
            bundle=bundle(None),
            config_path=Path("configs/tasks.toml"),
        )
    assert missing_prefix.value.code is ErrorCode.APPLE_RESOURCE_CONFLICT

    with pytest.raises(DomainError) as no_devices:
        concrete.ensure_profile(
            task=task(),
            intent=intent(),
            snapshot=snapshot(device("DISABLED", "hash", status="DISABLED")),
            certificate=certificate(),
            bundle=bundle(),
            config_path=Path("configs/tasks.toml"),
        )
    assert no_devices.value.code is ErrorCode.APPLE_RESOURCE_NOT_FOUND

    with pytest.raises(ConfigurationError) as preserve_source:
        concrete.ensure_profile(
            task=task(),
            intent=intent(EntitlementMode.PRESERVE_SOURCE),
            snapshot=snapshot(device("DEVICE", "hash")),
            certificate=certificate(),
            bundle=bundle(),
            config_path=Path("configs/tasks.toml"),
        )
    assert preserve_source.value.code is ErrorCode.CONFIG_INVALID
