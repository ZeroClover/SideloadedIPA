"""Typed errors crossing domain, configuration, and adapter boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sideloadedipa.domain.common import Diagnostic, FrozenJsonValue


class ErrorCode(StrEnum):
    DOMAIN_INVARIANT = "domain.invariant"
    IDENTIFIER_INVALID = "identifier.invalid"
    IDENTIFIER_NON_DESCENDANT = "identifier.non_descendant"
    IDENTIFIER_COLLISION = "identifier.collision"
    ENTITLEMENTS_POLICY_INVALID = "entitlements.policy_invalid"
    ENTITLEMENTS_UNDECLARED_DROP = "entitlements.undeclared_drop"
    ENTITLEMENTS_TEMPLATE_INVALID = "entitlements.template_invalid"
    ENTITLEMENTS_TEMPLATE_MISSING = "entitlements.template_missing"
    ENTITLEMENTS_TEMPLATE_PATH = "entitlements.template_path"
    SOURCE_RELEASE_INVALID = "source.release_invalid"
    SOURCE_ASSET_NOT_FOUND = "source.asset_not_found"
    SOURCE_ASSET_AMBIGUOUS = "source.asset_ambiguous"
    SOURCE_DIGEST_INVALID = "source.digest_invalid"
    SOURCE_DIGEST_MISMATCH = "source.digest_mismatch"
    SOURCE_DOWNLOAD_FAILED = "source.download_failed"
    WORKSPACE_INVALID = "workspace.invalid"
    ARCHIVE_INVALID = "archive.invalid"
    ARCHIVE_PATH_INVALID = "archive.path_invalid"
    ARCHIVE_PATH_DUPLICATE = "archive.path_duplicate"
    ARCHIVE_SPECIAL_FILE = "archive.special_file"
    ARCHIVE_LIMIT_EXCEEDED = "archive.limit_exceeded"
    INVENTORY_ROOT_AMBIGUOUS = "inventory.root_ambiguous"
    INVENTORY_METADATA_INVALID = "inventory.metadata_invalid"
    INVENTORY_EXECUTABLE_INVALID = "inventory.executable_invalid"
    INVENTORY_DUPLICATE_BUNDLE_ID = "inventory.duplicate_bundle_id"
    INVENTORY_ENTITLEMENTS_INVALID = "inventory.entitlements_invalid"
    INVENTORY_ENTITLEMENTS_DISAGREE = "inventory.entitlements_disagree"
    CONFIG_INVALID = "config.invalid"
    CONFIG_MISSING = "config.missing"
    ADAPTER_UNAVAILABLE = "adapter.unavailable"
    ADAPTER_TIMEOUT = "adapter.timeout"
    ADAPTER_COMMAND_FAILED = "adapter.command_failed"
    ADAPTER_RESPONSE_INVALID = "adapter.response_invalid"
    ADAPTER_VERSION_MISMATCH = "adapter.version_mismatch"
    APPLE_AUTHORIZATION_FAILED = "apple.authorization_failed"
    APPLE_RESOURCE_NOT_FOUND = "apple.resource_not_found"
    APPLE_RESOURCE_CONFLICT = "apple.resource_conflict"
    APPLE_RATE_LIMITED = "apple.rate_limited"
    APPLE_API_FAILED = "apple.api_failed"
    APPLE_PROFILE_DECODE_FAILED = "apple.profile_decode_failed"
    APPLE_PROFILE_INVALID = "apple.profile_invalid"
    APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED = "apple.profile_entitlement_unauthorized"
    SIGNING_PLAN_INVALID = "signing.plan_invalid"
    SIGNING_BACKEND_UNSUPPORTED = "signing.backend_unsupported"


class SideloadedIPAError(Exception):
    """Base exception with stable, serialization-safe context."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        task_name: str | None = None,
        bundle_id: str | None = None,
        remediation: str | None = None,
        safe_details: tuple[tuple[str, FrozenJsonValue], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.task_name = task_name
        self.bundle_id = bundle_id
        self.remediation = remediation
        self.safe_details = safe_details

    def to_diagnostic(self) -> Diagnostic:
        from sideloadedipa.domain.common import Diagnostic, DiagnosticSeverity

        return Diagnostic(
            code=self.code.value,
            severity=DiagnosticSeverity.ERROR,
            message=self.message,
            task_name=self.task_name,
            bundle_id=self.bundle_id,
            remediation=self.remediation,
            details=self.safe_details,
        )


class DomainError(SideloadedIPAError):
    """A validated domain invariant was not satisfied."""


class ConfigurationError(SideloadedIPAError):
    """User configuration is missing or invalid."""


class AdapterError(SideloadedIPAError):
    """An external process or service adapter failed."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        adapter: str,
        operation: str,
        task_name: str | None = None,
        bundle_id: str | None = None,
        remediation: str | None = None,
        safe_details: tuple[tuple[str, FrozenJsonValue], ...] = (),
    ) -> None:
        super().__init__(
            code,
            message,
            task_name=task_name,
            bundle_id=bundle_id,
            remediation=remediation,
            safe_details=(
                ("adapter", adapter),
                ("operation", operation),
                *safe_details,
            ),
        )
        self.adapter = adapter
        self.operation = operation
