"""Validated private inputs loaded from the package profile-sync stage."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from sideloadedipa.domain import (
    CertificateIdentity,
    FrozenJsonValue,
    ProfileManifestEntry,
    ProfileResourceManifest,
    ProfileType,
    ProfileValidationRequest,
    ProvisioningProfile,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.profile_validation import decode_mobileprovision, validate_provisioning_profile


class ProfileDecoder(Protocol):
    def __call__(
        self,
        profile_path: Path,
        *,
        bundle_id: str | None = None,
    ) -> Mapping[str, object]: ...


def _profile_error(entry: ProfileManifestEntry, message: str) -> DomainError:
    return DomainError(
        ErrorCode.APPLE_PROFILE_INVALID,
        message,
        bundle_id=entry.target_bundle_id,
        remediation="rerun package profile sync before signing",
    )


def _string(document: Mapping[str, object], key: str, entry: ProfileManifestEntry) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise _profile_error(entry, f"provisioning profile has invalid {key}")
    return value


def _strings(value: object, field: str, entry: ProfileManifestEntry) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise _profile_error(entry, f"provisioning profile has invalid {field}")
    return tuple(value)


def _entitlements(
    document: Mapping[str, object], entry: ProfileManifestEntry
) -> Mapping[str, object]:
    value = document.get("Entitlements")
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _profile_error(entry, "provisioning profile has invalid Entitlements")
    return value


def load_synced_profile(
    *,
    profile_root: Path,
    entry: ProfileManifestEntry,
    profile_type: ProfileType,
    certificate: CertificateIdentity,
    now: datetime,
    expected_entitlements: tuple[tuple[str, FrozenJsonValue], ...] | None = None,
    refresh_threshold: timedelta = timedelta(days=30),
    decoder: ProfileDecoder = decode_mobileprovision,
) -> ProvisioningProfile:
    """Decode and revalidate one private profile named by an authenticated manifest."""

    if entry.certificate_resource_id != certificate.resource_id:
        raise _profile_error(entry, "profile manifest references a different certificate")
    path = profile_root.joinpath(*entry.profile_path.parts)
    try:
        content = path.read_bytes()
    except OSError as error:
        raise _profile_error(entry, "synced provisioning profile is missing") from error
    if hashlib.sha256(content).hexdigest() != entry.profile_sha256:
        raise _profile_error(entry, "synced provisioning profile digest changed after sync")
    document = decoder(path, bundle_id=entry.target_bundle_id)
    entitlements = _entitlements(document, entry)
    application_identifier = entitlements.get("application-identifier")
    if not isinstance(application_identifier, str) or not application_identifier:
        raise _profile_error(entry, "provisioning profile has invalid application-identifier")
    teams = _strings(document.get("TeamIdentifier"), "TeamIdentifier", entry)
    if len(teams) != 1:
        raise _profile_error(entry, "provisioning profile must contain exactly one Team ID")
    device_ids = tuple(
        sorted(
            hashlib.sha256(value.encode()).hexdigest()
            for value in _strings(document.get("ProvisionedDevices"), "ProvisionedDevices", entry)
        )
    )
    selected_entitlements = (
        expected_entitlements
        if expected_entitlements is not None
        else normalize_entitlements(entitlements).values
    )
    request = ProfileValidationRequest(
        entry.profile_resource_id,
        entry.target_bundle_id,
        application_identifier,
        teams[0],
        profile_type,
        certificate.certificate_sha256,
        device_ids,
        entry.profile_path,
        selected_entitlements,
    )
    validated = validate_provisioning_profile(
        document,
        content,
        request,
        now=now,
        refresh_threshold=refresh_threshold,
    )
    if validated.expires_at != entry.expires_at:
        raise _profile_error(entry, "profile expiry differs from the authenticated manifest")
    return validated


def load_synced_profiles(
    *,
    profile_root: Path,
    manifest: ProfileResourceManifest,
    profile_type: ProfileType,
    certificate: CertificateIdentity,
    now: datetime,
    expected_entitlements: Mapping[str, tuple[tuple[str, FrozenJsonValue], ...]] | None = None,
    refresh_threshold: timedelta = timedelta(days=30),
    decoder: ProfileDecoder = decode_mobileprovision,
) -> tuple[ProvisioningProfile, ...]:
    expected = expected_entitlements or {}
    unknown = tuple(sorted(set(expected) - {entry.target_bundle_id for entry in manifest.entries}))
    if unknown:
        raise DomainError(
            ErrorCode.SIGNING_PLAN_INVALID,
            "expected entitlements reference a bundle absent from the profile manifest",
            task_name=manifest.task_name,
            safe_details=(("bundle_ids", unknown),),
        )
    return tuple(
        load_synced_profile(
            profile_root=profile_root,
            entry=entry,
            profile_type=profile_type,
            certificate=certificate,
            now=now,
            expected_entitlements=expected.get(entry.target_bundle_id),
            refresh_threshold=refresh_threshold,
            decoder=decoder,
        )
        for entry in manifest.entries
    )
