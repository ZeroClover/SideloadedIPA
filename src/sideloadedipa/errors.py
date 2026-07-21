"""Typed errors crossing domain, configuration, and adapter boundaries."""

from __future__ import annotations

from enum import StrEnum

from sideloadedipa.domain.common import Diagnostic, DiagnosticSeverity, FrozenJsonValue


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
    CONFIG_INVALID = "config.invalid"
    CONFIG_MISSING = "config.missing"
    ADAPTER_UNAVAILABLE = "adapter.unavailable"
    ADAPTER_TIMEOUT = "adapter.timeout"
    ADAPTER_COMMAND_FAILED = "adapter.command_failed"
    ADAPTER_RESPONSE_INVALID = "adapter.response_invalid"


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
