"""Apple signing-resource and provisioning-profile values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

from sideloadedipa.domain.common import Diagnostic, FrozenJsonValue
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
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class AppleResourcePlan:
    snapshot_sha256: str
    operations: tuple[AppleOperation, ...]
    resources: tuple[AppleResource, ...] = ()


@dataclass(frozen=True, slots=True)
class CertificateIdentity:
    resource_id: str
    serial_number: str
    public_key_sha256: str
    certificate_sha256: str
    expires_at: datetime


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
