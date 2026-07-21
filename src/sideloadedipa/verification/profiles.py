"""Embedded profile and bundle identity verification."""

from __future__ import annotations

import hashlib
import plistlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol

from sideloadedipa.domain import (
    Diagnostic,
    DiagnosticSeverity,
    ProfileValidationRequest,
    ProvisioningProfile,
    SigningPlan,
    VerificationFinding,
)
from sideloadedipa.errors import ErrorCode, SideloadedIPAError
from sideloadedipa.ipa.archive import extract_ipa_safely
from sideloadedipa.profile_validation import decode_and_validate_provisioning_profile
from sideloadedipa.workspace import task_workspace


class EmbeddedProfileValidator(Protocol):
    def validate(self, path: Path, request: ProfileValidationRequest) -> ProvisioningProfile: ...


@dataclass(frozen=True, slots=True)
class OpenSSLEmbeddedProfileValidator:
    now: datetime
    refresh_threshold: timedelta

    def validate(self, path: Path, request: ProfileValidationRequest) -> ProvisioningProfile:
        return decode_and_validate_provisioning_profile(
            path,
            request,
            now=self.now,
            refresh_threshold=self.refresh_threshold,
        )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diagnostic(
    plan: SigningPlan,
    bundle_id: str | None,
    code: str,
    message: str,
) -> Diagnostic:
    return Diagnostic(
        code,
        DiagnosticSeverity.ERROR,
        message,
        task_name=plan.task_name,
        bundle_id=bundle_id,
        remediation="replace the signed artifact or embedded development profile and retry",
    )


def _finding(
    path: PurePosixPath,
    check: str,
    passed: bool,
    *,
    expected: str | None = None,
    actual: str | None = None,
    diagnostic: Diagnostic | None = None,
) -> VerificationFinding:
    return VerificationFinding(
        path,
        check,
        passed,
        expected,
        actual,
        (diagnostic,) if diagnostic is not None else (),
    )


def _info_bundle_id(path: Path) -> str | None:
    try:
        document = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError):
        return None
    if not isinstance(document, Mapping):
        return None
    value = document.get("CFBundleIdentifier")
    return value if isinstance(value, str) and value else None


def verify_signed_profiles(
    plan: SigningPlan,
    signed_ipa: Path,
    profiles: tuple[ProvisioningProfile, ...],
    *,
    validator: EmbeddedProfileValidator,
) -> tuple[VerificationFinding, ...]:
    """Verify exact bundle IDs and embedded development-profile identity."""

    profiles_by_id = {value.resource_id: value for value in profiles}
    workspace_base = signed_ipa.parent / ".sideloadedipa-profile-verification"
    remove_workspace_base = not workspace_base.exists()
    try:
        with task_workspace(workspace_base, plan.task_name) as workspace:
            extract_ipa_safely(signed_ipa, workspace.extracted)
            findings: list[VerificationFinding] = []
            for node in plan.nodes:
                if node.profile_resource_id is None or node.target_bundle_id is None:
                    continue
                bundle = workspace.extracted.joinpath(*node.source_path.parts)
                actual_bundle_id = _info_bundle_id(bundle / "Info.plist")
                bundle_passed = actual_bundle_id == node.target_bundle_id
                findings.append(
                    _finding(
                        node.source_path,
                        "bundle-identifier",
                        bundle_passed,
                        expected=hashlib.sha256(node.target_bundle_id.encode()).hexdigest(),
                        actual=(
                            hashlib.sha256(actual_bundle_id.encode()).hexdigest()
                            if actual_bundle_id is not None
                            else None
                        ),
                        diagnostic=(
                            None
                            if bundle_passed
                            else _diagnostic(
                                plan,
                                node.target_bundle_id,
                                "verification.bundle_identifier",
                                "signed bundle identifier does not match the plan",
                            )
                        ),
                    )
                )

                embedded = bundle / "embedded.mobileprovision"
                actual_profile_sha256 = _digest(embedded) if embedded.is_file() else None
                digest_passed = (
                    node.profile_sha256 is not None and actual_profile_sha256 == node.profile_sha256
                )
                findings.append(
                    _finding(
                        node.source_path,
                        "embedded-profile-sha256",
                        digest_passed,
                        expected=node.profile_sha256,
                        actual=actual_profile_sha256,
                        diagnostic=(
                            None
                            if digest_passed
                            else _diagnostic(
                                plan,
                                node.target_bundle_id,
                                "verification.embedded_profile",
                                "embedded profile is missing or differs from the plan",
                            )
                        ),
                    )
                )

                planned_profile = profiles_by_id.get(node.profile_resource_id)
                if planned_profile is None or not embedded.is_file():
                    findings.append(
                        _finding(
                            node.source_path,
                            "embedded-profile-validation",
                            False,
                            diagnostic=_diagnostic(
                                plan,
                                node.target_bundle_id,
                                "verification.profile_identity",
                                "planned or embedded profile evidence is missing",
                            ),
                        )
                    )
                    continue
                request = ProfileValidationRequest(
                    planned_profile.resource_id,
                    node.target_bundle_id,
                    planned_profile.application_identifier,
                    planned_profile.team_id,
                    planned_profile.profile_type,
                    plan.certificate_sha256,
                    planned_profile.device_ids,
                    planned_profile.path,
                    node.expected_entitlements,
                )
                try:
                    validated = validator.validate(embedded, request)
                    passed = (
                        validated.resource_id == planned_profile.resource_id
                        and validated.team_id == planned_profile.team_id
                        and validated.application_identifier
                        == planned_profile.application_identifier
                        and validated.certificate_sha256 == plan.certificate_sha256
                    )
                    error_diagnostic = None
                except SideloadedIPAError as error:
                    passed = False
                    error_diagnostic = error.to_diagnostic()
                findings.append(
                    _finding(
                        node.source_path,
                        "embedded-profile-validation",
                        passed,
                        expected=node.profile_sha256,
                        actual=actual_profile_sha256,
                        diagnostic=(error_diagnostic if not passed else None),
                    )
                )
            return tuple(findings)
    finally:
        if remove_workspace_base:
            try:
                workspace_base.rmdir()
            except OSError:
                pass
