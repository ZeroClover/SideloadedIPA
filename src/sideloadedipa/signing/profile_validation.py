"""CMS decoding and fail-closed validation for iOS development profiles."""

from __future__ import annotations

import hashlib
import plistlib
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sideloadedipa.domain import (
    FrozenJsonValue,
    ProfileType,
    ProfileValidationRequest,
    ProvisioningProfile,
    normalize_entitlements,
    thaw_json,
)
from sideloadedipa.errors import AdapterError, DomainError, ErrorCode
from sideloadedipa.util.atomics import utc_now
from sideloadedipa.util.subprocesses import SubprocessRunner
from sideloadedipa.verification.entitlements import (
    EntitlementComparisonMode,
    compare_entitlements,
)

_APPLICATION_IDENTIFIER = "application-identifier"
_TEAM_IDENTIFIER = "com.apple.developer.team-identifier"


@dataclass(frozen=True, slots=True)
class MobileProvisionValidator:
    refresh_threshold: timedelta
    now: datetime | None = None
    runner: SubprocessRunner | None = None

    def validate(self, content: bytes, request: ProfileValidationRequest) -> ProvisioningProfile:
        return validate_mobileprovision_content(
            content,
            request,
            now=utc_now() if self.now is None else self.now,
            refresh_threshold=self.refresh_threshold,
            runner=self.runner,
        )


def _invalid_profile(
    message: str,
    request: ProfileValidationRequest,
    *,
    safe_details: tuple[tuple[str, FrozenJsonValue], ...] = (),
) -> DomainError:
    return DomainError(
        ErrorCode.APPLE_PROFILE_INVALID,
        message,
        bundle_id=request.target_bundle_id,
        remediation="generate and download a replacement development profile",
        safe_details=safe_details,
    )


def decode_mobileprovision(
    profile_path: Path,
    *,
    runner: SubprocessRunner | None = None,
    bundle_id: str | None = None,
) -> Mapping[str, object]:
    """Verify a DER CMS signature and return its embedded plist document."""

    command_runner = runner or SubprocessRunner(default_timeout_seconds=30)
    with tempfile.TemporaryDirectory(prefix="sideloadedipa-profile-") as directory:
        decoded_path = Path(directory) / "profile.plist"
        try:
            command_runner.run(
                [
                    "openssl",
                    "cms",
                    "-verify",
                    "-verify_retcode",
                    "-binary",
                    "-inform",
                    "DER",
                    "-in",
                    profile_path,
                    "-noverify",
                    "-out",
                    decoded_path,
                ],
                timeout_seconds=30,
                path_redactions=(profile_path, decoded_path),
            )
            payload = decoded_path.read_bytes()
            decoded = plistlib.loads(payload)
        except AdapterError as error:
            raise AdapterError(
                ErrorCode.APPLE_PROFILE_DECODE_FAILED,
                "provisioning profile CMS signature could not be verified",
                adapter="openssl",
                operation="verify-mobileprovision",
                bundle_id=bundle_id,
                remediation="download a fresh profile from Apple and retry",
                safe_details=(("cause_code", error.code.value),),
            ) from error
        except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as error:
            raise AdapterError(
                ErrorCode.APPLE_PROFILE_DECODE_FAILED,
                "verified provisioning profile payload is not a readable plist",
                adapter="openssl",
                operation="decode-mobileprovision",
                bundle_id=bundle_id,
                remediation="download a fresh profile from Apple and retry",
                safe_details=(("cause", type(error).__name__),),
            ) from error
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise AdapterError(
            ErrorCode.APPLE_PROFILE_DECODE_FAILED,
            "verified provisioning profile payload is not a property-list dictionary",
            adapter="openssl",
            operation="decode-mobileprovision",
            bundle_id=bundle_id,
            remediation="download a fresh profile from Apple and retry",
        )
    return decoded


def _utc_datetime(value: object, field: str, request: ProfileValidationRequest) -> datetime:
    if not isinstance(value, datetime):
        raise _invalid_profile(
            "provisioning profile has an invalid date",
            request,
            safe_details=(("field", field),),
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _string_list(value: object, field: str, request: ProfileValidationRequest) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _invalid_profile(
            "provisioning profile has an invalid string array",
            request,
            safe_details=(("field", field),),
        )
    if any(not isinstance(item, str) for item in value):
        raise _invalid_profile(
            "provisioning profile has an invalid string array",
            request,
            safe_details=(("field", field),),
        )
    return tuple(value)


def validate_entitlement_authorization(
    profile_entitlements: Mapping[str, object],
    expected_entitlements: Mapping[str, object],
    request: ProfileValidationRequest,
) -> None:
    """Require every claimed entitlement value to be allowed by the profile."""

    validate_expected_entitlements(
        profile_entitlements,
        expected_entitlements,
        bundle_id=request.target_bundle_id,
    )


def validate_expected_entitlements(
    profile_entitlements: Mapping[str, object],
    expected_entitlements: Mapping[str, object],
    *,
    bundle_id: str,
) -> None:
    """Apply provisioning-profile authorization semantics to planned entitlements."""

    comparison = compare_entitlements(
        expected_entitlements,
        profile_entitlements,
        mode=EntitlementComparisonMode.PROFILE_AUTHORIZATION,
    )
    if comparison.passed:
        return
    difference = comparison.differences[0]
    raise DomainError(
        ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED,
        "provisioning profile does not authorize a required entitlement value",
        bundle_id=bundle_id,
        remediation="enable the capability or replace the profile before signing",
        safe_details=(
            ("key", difference.path),
            ("reason", difference.reason),
            ("expected_sha256", difference.expected_sha256),
            ("actual_sha256", difference.actual_sha256),
        ),
    )


def validate_provisioning_profile(
    document: Mapping[str, object],
    content: bytes,
    request: ProfileValidationRequest,
    *,
    now: datetime,
    refresh_threshold: timedelta,
) -> ProvisioningProfile:
    """Validate decoded profile identity, eligibility, dates, and authorization."""

    if now.tzinfo is None or refresh_threshold < timedelta(0):
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "profile validation requires an aware clock and non-negative refresh threshold",
            bundle_id=request.target_bundle_id,
        )
    now_utc = now.astimezone(timezone.utc)

    name = document.get("Name")
    if not isinstance(name, str) or not name:
        raise _invalid_profile("provisioning profile has no valid name", request)
    team_ids = _string_list(document.get("TeamIdentifier"), "TeamIdentifier", request)
    if team_ids != (request.team_id,):
        raise _invalid_profile(
            "provisioning profile team does not match the signing plan",
            request,
            safe_details=(("expected_team_id", request.team_id),),
        )

    entitlements = document.get("Entitlements")
    if not isinstance(entitlements, Mapping) or any(
        not isinstance(key, str) for key in entitlements
    ):
        raise _invalid_profile("provisioning profile has no valid entitlement dictionary", request)
    profile_entitlements = {str(key): value for key, value in entitlements.items()}
    if profile_entitlements.get(_APPLICATION_IDENTIFIER) != request.application_identifier:
        raise _invalid_profile(
            "provisioning profile application identifier does not match the signing plan",
            request,
            safe_details=(("expected_application_identifier", request.application_identifier),),
        )
    if profile_entitlements.get(_TEAM_IDENTIFIER) != request.team_id:
        raise _invalid_profile(
            "provisioning profile entitlement team does not match the signing plan",
            request,
            safe_details=(("expected_team_id", request.team_id),),
        )

    if not request.application_identifier.endswith(f".{request.target_bundle_id}"):
        raise DomainError(
            ErrorCode.DOMAIN_INVARIANT,
            "planned application identifier does not contain the exact target bundle identifier",
            bundle_id=request.target_bundle_id,
        )
    expected_prefix = request.application_identifier[: -len(request.target_bundle_id)]
    prefixes = _string_list(
        document.get("ApplicationIdentifierPrefix"), "ApplicationIdentifierPrefix", request
    )
    if prefixes != (expected_prefix.rstrip("."),):
        raise _invalid_profile(
            "provisioning profile App ID prefix does not match the application identifier",
            request,
        )

    if request.profile_type is not ProfileType.IOS_APP_DEVELOPMENT:
        raise _invalid_profile("unsupported provisioning profile type", request)
    if (
        profile_entitlements.get("get-task-allow") is not True
        or document.get("ProvisionsAllDevices") is True
    ):
        raise _invalid_profile(
            "provisioning profile is not an iOS app development profile",
            request,
            safe_details=(("expected_profile_type", request.profile_type.value),),
        )

    certificates = document.get("DeveloperCertificates")
    if not isinstance(certificates, Sequence) or isinstance(certificates, (str, bytes, bytearray)):
        raise _invalid_profile("provisioning profile has no valid certificate set", request)
    if any(not isinstance(certificate, (bytes, bytearray)) for certificate in certificates):
        raise _invalid_profile("provisioning profile has no valid certificate set", request)
    certificate_hashes = tuple(
        sorted(hashlib.sha256(bytes(certificate)).hexdigest() for certificate in certificates)
    )
    if certificate_hashes != (request.certificate_sha256,):
        raise _invalid_profile(
            "provisioning profile certificate set does not match the signing identity",
            request,
            safe_details=(("expected_certificate_sha256", request.certificate_sha256),),
        )

    device_udids = _string_list(document.get("ProvisionedDevices"), "ProvisionedDevices", request)
    device_hashes = tuple(
        sorted(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in device_udids)
    )
    if device_hashes != tuple(sorted(request.device_udid_sha256)):
        raise _invalid_profile(
            "provisioning profile enabled-device set does not match Apple state",
            request,
            safe_details=(
                ("expected_device_count", len(request.device_udid_sha256)),
                ("actual_device_count", len(device_hashes)),
            ),
        )

    created_at = _utc_datetime(document.get("CreationDate"), "CreationDate", request)
    expires_at = _utc_datetime(document.get("ExpirationDate"), "ExpirationDate", request)
    if created_at > now_utc or expires_at <= created_at:
        raise _invalid_profile("provisioning profile has an invalid validity interval", request)
    if expires_at <= now_utc + refresh_threshold:
        raise _invalid_profile(
            "provisioning profile is expired or inside the refresh window",
            request,
            safe_details=(
                ("expires_at", expires_at.isoformat()),
                ("refresh_threshold_seconds", int(refresh_threshold.total_seconds())),
            ),
        )

    expected_entitlements = {key: thaw_json(value) for key, value in request.expected_entitlements}
    validate_entitlement_authorization(profile_entitlements, expected_entitlements, request)
    normalized_entitlements = normalize_entitlements(profile_entitlements)
    return ProvisioningProfile(
        resource_id=request.resource_id,
        name=name,
        profile_type=request.profile_type,
        bundle_id=request.target_bundle_id,
        application_identifier=request.application_identifier,
        team_id=request.team_id,
        certificate_sha256=request.certificate_sha256,
        device_ids=device_hashes,
        created_at=created_at,
        expires_at=expires_at,
        profile_sha256=hashlib.sha256(content).hexdigest(),
        path=request.path,
        entitlements=normalized_entitlements.values,
    )


def decode_and_validate_provisioning_profile(
    profile_path: Path,
    request: ProfileValidationRequest,
    *,
    now: datetime,
    refresh_threshold: timedelta,
    runner: SubprocessRunner | None = None,
) -> ProvisioningProfile:
    try:
        content = profile_path.read_bytes()
    except OSError as error:
        raise AdapterError(
            ErrorCode.APPLE_PROFILE_DECODE_FAILED,
            "provisioning profile could not be read",
            adapter="filesystem",
            operation="read-mobileprovision",
            bundle_id=request.target_bundle_id,
            remediation="download the profile into the private profile workspace and retry",
            safe_details=(("cause", type(error).__name__),),
        ) from error
    document = decode_mobileprovision(
        profile_path,
        runner=runner,
        bundle_id=request.target_bundle_id,
    )
    return validate_provisioning_profile(
        document,
        content,
        request,
        now=now,
        refresh_threshold=refresh_threshold,
    )


def validate_mobileprovision_content(
    content: bytes,
    request: ProfileValidationRequest,
    *,
    now: datetime,
    refresh_threshold: timedelta,
    runner: SubprocessRunner | None = None,
) -> ProvisioningProfile:
    """Validate in-memory profile content without exposing it outside a private temp directory."""

    with tempfile.TemporaryDirectory(prefix="sideloadedipa-profile-") as directory:
        profile_path = Path(directory) / "profile.mobileprovision"
        profile_path.write_bytes(content)
        profile_path.chmod(0o600)
        document = decode_mobileprovision(
            profile_path,
            runner=runner,
            bundle_id=request.target_bundle_id,
        )
    return validate_provisioning_profile(
        document,
        content,
        request,
        now=now,
        refresh_threshold=refresh_threshold,
    )
