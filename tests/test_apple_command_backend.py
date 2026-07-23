"""Tests for profile requests built by the concrete Apple command backend."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

import sideloadedipa.apple.backend as backend_module
import sideloadedipa.apple.expected_entitlements as expected_module
from sideloadedipa.adapters.apple import ProfileReconciliationResult, ProfileSyncRequest
from sideloadedipa.apple.commands import AscAppleCommandBackend
from sideloadedipa.apple.intents import BundleResourceIntent
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
from sideloadedipa.signing.profile_storage import profile_relative_path

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
    captured: dict[str, object] = {}
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

        def ensure(
            self,
            request: ProfileSyncRequest,
            *,
            profiles: tuple[object, ...] | None = None,
        ) -> ProfileReconciliationResult:
            captured["request"] = request
            captured["profiles"] = profiles
            return expected_result

    concrete = backend()
    monkeypatch.setattr(backend_module, "ProfileReconciler", RecordingReconciler)

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
    assert isinstance(request, ProfileSyncRequest)
    entitlements = {
        key: thaw_json(value) for key, value in request.validation.expected_entitlements
    }
    assert result is expected_result
    assert captured["profiles"] == ()
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


def test_backend_composes_and_delegates_apple_gateways(monkeypatch: pytest.MonkeyPatch) -> None:
    client = object()
    calls: list[tuple[str, object]] = []

    class Bundles:
        def ensure(self, *, identifier: str, name: str) -> AppleBundleIdentifierState:
            calls.append(("bundle", (identifier, name)))
            return bundle()

    class Capabilities:
        def ensure(self, **values: object) -> None:
            calls.append(("capability", values))

    class Collector:
        def __init__(self, value: object) -> None:
            assert value is client

        def collect(self) -> AppleStateSnapshot:
            return snapshot()

    monkeypatch.setattr(backend_module, "AscBundleIdGateway", lambda value: ("bundles", value))
    monkeypatch.setattr(backend_module, "BundleIdReconciler", lambda value: Bundles())
    monkeypatch.setattr(
        backend_module, "AscCapabilityGateway", lambda value: ("capabilities", value)
    )
    monkeypatch.setattr(backend_module, "CapabilityReconciler", lambda value: Capabilities())
    monkeypatch.setattr(backend_module, "AscProfileGateway", lambda value: ("profiles", value))
    monkeypatch.setattr(backend_module, "AppleStateCollector", Collector)

    concrete = backend_module.AscAppleCommandBackend(client)

    assert concrete.collect() == snapshot()
    assert concrete.ensure_bundle(intent()) == bundle()
    concrete.ensure_capability(bundle=bundle(), capability_type="APP_GROUPS")
    assert concrete.profiles == ("profiles", client)
    assert calls == [
        ("bundle", ("io.example.app", "Example")),
        (
            "capability",
            {
                "bundle_resource_id": "BUNDLE_ONE",
                "bundle_id": "io.example.app",
                "capability_type": "APP_GROUPS",
            },
        ),
    ]


def test_backend_requires_and_returns_environment_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    concrete = backend()
    monkeypatch.setattr(
        backend_module, "certificate_identity_from_environment", lambda current: None
    )

    with pytest.raises(ConfigurationError) as missing:
        concrete.resolve_certificate(snapshot())
    assert missing.value.code is ErrorCode.CONFIG_MISSING

    expected = certificate()
    monkeypatch.setattr(
        backend_module,
        "certificate_identity_from_environment",
        lambda current: expected,
    )
    assert concrete.resolve_certificate(snapshot()) is expected


def test_expected_entitlement_inputs_fail_closed_and_resolve_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(DomainError) as empty_prefix:
        expected_module.application_identifier_prefix(bundle("."))
    assert empty_prefix.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID

    missing_template = replace(
        intent(),
        entitlement_policy=EntitlementPolicy(EntitlementMode.TEMPLATE),
    )
    with pytest.raises(ConfigurationError) as missing:
        expected_module.expected_entitlements(
            task=task(),
            intent=missing_template,
            team_id="TEAMID1234",
            app_identifier_prefix="PREFIX9876.",
            config_path=Path("configs/tasks.toml"),
        )
    assert missing.value.code is ErrorCode.ENTITLEMENTS_TEMPLATE_MISSING

    configs = tmp_path / "configs"
    configs.mkdir()
    assert expected_module._repository_root(configs / "tasks.toml") == tmp_path
    monkeypatch.chdir(tmp_path)
    assert expected_module._repository_root(tmp_path / "custom.toml") == tmp_path

    seen: dict[str, object] = {}

    def load_template(root, template_path, context):  # type: ignore[no-untyped-def]
        seen.update(root=root, template_path=template_path, context=context)
        return {"application-identifier": "PREFIX9876.io.example.app"}

    monkeypatch.setattr(expected_module, "load_entitlement_template", load_template)
    template_intent = replace(
        intent(),
        entitlement_policy=EntitlementPolicy(
            EntitlementMode.TEMPLATE,
            PurePosixPath("templates/example.plist"),
        ),
    )
    values = expected_module.expected_entitlements(
        task=task(),
        intent=template_intent,
        team_id="TEAMID1234",
        app_identifier_prefix="PREFIX9876.",
        config_path=configs / "tasks.toml",
    )

    assert values == {"application-identifier": "PREFIX9876.io.example.app"}
    assert seen["root"] == tmp_path
    assert seen["template_path"] == PurePosixPath("templates/example.plist")
