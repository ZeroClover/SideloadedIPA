"""Apple command backend protocol and App Store Connect composition."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Protocol

from sideloadedipa.adapters.apple import (
    AppleStateCollector,
    AscBundleIdGateway,
    AscCapabilityGateway,
    AscClient,
    AscProfileGateway,
    BundleIdReconciler,
    CapabilityReconciler,
    ProfileReconciler,
    ProfileReconciliationResult,
    ProfileSyncRequest,
)
from sideloadedipa.apple.expected_entitlements import (
    application_identifier_prefix,
    expected_entitlements,
)
from sideloadedipa.apple.intents import BundleResourceIntent
from sideloadedipa.apple.state_probe import certificate_identity_from_environment
from sideloadedipa.domain import (
    AppleBundleIdentifierState,
    AppleStateSnapshot,
    CertificateIdentity,
    ProfileValidationRequest,
    Task,
    normalize_entitlements,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.signing.profile_storage import profile_relative_path
from sideloadedipa.signing.profile_validation import MobileProvisionValidator

_PROFILE_REFRESH_THRESHOLD = timedelta(days=30)
_IOS_DEVICE_CLASSES = frozenset({"IPHONE", "IPAD"})


class AppleCommandBackend(Protocol):
    def collect(self) -> AppleStateSnapshot: ...

    def resolve_certificate(self, snapshot: AppleStateSnapshot) -> CertificateIdentity: ...

    def ensure_bundle(self, intent: BundleResourceIntent) -> AppleBundleIdentifierState: ...

    def ensure_capability(
        self,
        *,
        bundle: AppleBundleIdentifierState,
        capability_type: str,
    ) -> None: ...

    def ensure_profile(
        self,
        *,
        task: Task,
        intent: BundleResourceIntent,
        snapshot: AppleStateSnapshot,
        certificate: CertificateIdentity,
        bundle: AppleBundleIdentifierState,
        config_path: Path,
    ) -> ProfileReconciliationResult: ...


class AscAppleCommandBackend:
    def __init__(self, client: AscClient | None = None) -> None:
        self.client = client or AscClient()
        self.bundle_ids = BundleIdReconciler(AscBundleIdGateway(self.client))
        self.capabilities = CapabilityReconciler(AscCapabilityGateway(self.client))
        self.profiles = AscProfileGateway(self.client)

    def collect(self) -> AppleStateSnapshot:
        return AppleStateCollector(self.client).collect()

    def resolve_certificate(self, snapshot: AppleStateSnapshot) -> CertificateIdentity:
        identity = certificate_identity_from_environment(snapshot)
        if identity is None:
            raise ConfigurationError(
                ErrorCode.CONFIG_MISSING,
                "Apple resource commands require the development certificate P12",
                remediation=(
                    "set APPLE_DEV_CERT_P12_ENCODED and APPLE_DEV_CERT_PASSWORD in the CI environment"
                ),
            )
        return identity

    def ensure_bundle(self, intent: BundleResourceIntent) -> AppleBundleIdentifierState:
        return self.bundle_ids.ensure(
            identifier=intent.target_bundle_id,
            name=intent.display_name,
        )

    def ensure_capability(
        self,
        *,
        bundle: AppleBundleIdentifierState,
        capability_type: str,
    ) -> None:
        self.capabilities.ensure(
            bundle_resource_id=bundle.resource_id,
            bundle_id=bundle.identifier,
            capability_type=capability_type,
        )

    def ensure_profile(
        self,
        *,
        task: Task,
        intent: BundleResourceIntent,
        snapshot: AppleStateSnapshot,
        certificate: CertificateIdentity,
        bundle: AppleBundleIdentifierState,
        config_path: Path,
    ) -> ProfileReconciliationResult:
        prefix = application_identifier_prefix(bundle)
        expected = expected_entitlements(
            task=task,
            intent=intent,
            team_id=certificate.team_id,
            app_identifier_prefix=prefix,
            config_path=config_path,
        )
        devices = tuple(
            device
            for device in snapshot.devices
            if device.status == "ENABLED" and device.device_class in _IOS_DEVICE_CLASSES
        )
        if not devices:
            raise DomainError(
                ErrorCode.APPLE_RESOURCE_NOT_FOUND,
                "no enabled iPhone or iPad is available for a development profile",
                bundle_id=intent.target_bundle_id,
                remediation="register and enable an iPhone or iPad in the Apple Developer account",
            )
        validation = ProfileValidationRequest(
            resource_id="",
            target_bundle_id=intent.target_bundle_id,
            application_identifier=f"{prefix}{intent.target_bundle_id}",
            team_id=certificate.team_id,
            profile_type=intent.profile_type,
            certificate_sha256=certificate.certificate_sha256,
            device_udid_sha256=tuple(sorted(device.udid_sha256 for device in devices)),
            path=profile_relative_path(task.task_name, intent.target_bundle_id),
            expected_entitlements=normalize_entitlements(expected).values,
        )
        reconciler = ProfileReconciler(
            self.profiles,
            MobileProvisionValidator(refresh_threshold=_PROFILE_REFRESH_THRESHOLD),
        )
        return reconciler.ensure(
            ProfileSyncRequest(
                base_name=intent.profile_name,
                bundle_resource_id=bundle.resource_id,
                certificate_resource_id=certificate.resource_id,
                device_resource_ids=tuple(sorted(device.resource_id for device in devices)),
                validation=validation,
            )
        )
