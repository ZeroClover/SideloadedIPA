"""Apple signing-resource and provisioning-profile values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath

from sideloadedipa.domain.common import Diagnostic, FrozenJsonObject, FrozenJsonValue
from sideloadedipa.domain.config import ProfileType


class AppleResourceKind(StrEnum):
    BUNDLE_ID = "bundle-id"
    CAPABILITY = "capability"
    APP_GROUP = "app-group"
    CERTIFICATE = "certificate"
    DEVICE = "device"
    PROFILE = "profile"


class OperationDisposition(StrEnum):
    NO_OP = "no-op"
    SAFE_AUTOMATIC = "safe-automatic"
    MANUAL_REQUIRED = "manual-required"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class AppleBundleIdentifierState:
    resource_id: str
    identifier: str
    name: str
    platform: str
    seed_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppleCapabilityState:
    resource_id: str
    bundle_resource_id: str
    capability_type: str
    settings: tuple[FrozenJsonObject, ...] = ()


@dataclass(frozen=True, slots=True)
class AppleCertificateState:
    resource_id: str
    name: str
    certificate_type: str
    display_name: str | None
    serial_number: str | None
    platform: str | None
    expiration_date: str | None
    certificate_sha256: str | None


@dataclass(frozen=True, slots=True)
class AppleDeviceState:
    resource_id: str
    name: str
    platform: str
    status: str
    device_class: str
    udid_sha256: str


@dataclass(frozen=True, slots=True)
class AppleProfileState:
    resource_id: str
    name: str
    platform: str | None
    profile_type: str
    profile_state: str | None
    uuid: str | None
    created_date: str | None
    expiration_date: str | None
    profile_sha256: str | None
    bundle_resource_id: str
    certificate_resource_ids: tuple[str, ...]
    device_resource_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AppleStateSnapshot:
    snapshot_sha256: str
    bundle_ids: tuple[AppleBundleIdentifierState, ...]
    capabilities: tuple[AppleCapabilityState, ...]
    certificates: tuple[AppleCertificateState, ...]
    devices: tuple[AppleDeviceState, ...]
    profiles: tuple[AppleProfileState, ...]


@dataclass(frozen=True, slots=True)
class AppleResource:
    kind: AppleResourceKind
    resource_id: str
    name: str
    attributes: tuple[tuple[str, FrozenJsonValue], ...] = ()


@dataclass(frozen=True, slots=True)
class AppleOperation:
    disposition: OperationDisposition
    resource_kind: AppleResourceKind
    action: str
    target: str
    existing_resource_id: str | None = None
    bundle_id: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class AppleResourcePlan:
    snapshot_sha256: str
    operations: tuple[AppleOperation, ...]
    resources: tuple[AppleResource, ...] = ()


@dataclass(frozen=True, slots=True)
class AppleResourceRequirement:
    resource_kind: AppleResourceKind
    action: str
    target: str
    bundle_id: str | None
    matching_resource_ids: tuple[str, ...]
    missing_disposition: OperationDisposition
    remediation: str
    satisfied_without_resource: bool = False


@dataclass(frozen=True, slots=True)
class CertificateIdentity:
    resource_id: str
    serial_number: str
    public_key_sha256: str
    certificate_sha256: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class P12CertificateIdentity:
    serial_number: str
    public_key_sha256: str
    certificate_sha256: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CertificateMaterial:
    identity: CertificateIdentity
    certificate_path: Path
    private_key_path: Path


@dataclass(frozen=True, slots=True)
class ProvisioningProfile:
    resource_id: str
    name: str
    profile_type: ProfileType
    bundle_id: str
    application_identifier: str
    team_id: str
    certificate_sha256: str
    device_ids: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    profile_sha256: str
    path: PurePosixPath
    entitlements: tuple[tuple[str, FrozenJsonValue], ...]


@dataclass(frozen=True, slots=True)
class ProfileValidationRequest:
    resource_id: str
    target_bundle_id: str
    application_identifier: str
    team_id: str
    profile_type: ProfileType
    certificate_sha256: str
    device_udid_sha256: tuple[str, ...]
    path: PurePosixPath
    expected_entitlements: tuple[tuple[str, FrozenJsonValue], ...]


@dataclass(frozen=True, slots=True)
class ProfileManifestEntry:
    target_bundle_id: str
    bundle_resource_id: str
    profile_resource_id: str
    certificate_resource_id: str
    profile_path: PurePosixPath
    profile_sha256: str
    device_set_sha256: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileResourceManifest:
    schema_version: int
    task_name: str
    snapshot_sha256: str
    entries: tuple[ProfileManifestEntry, ...]
    manifest_sha256: str
