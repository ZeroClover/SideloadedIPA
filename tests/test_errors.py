"""Tests for stable typed pipeline errors."""

from __future__ import annotations

from sideloadedipa.domain import DiagnosticSeverity
from sideloadedipa.errors import AdapterError, ConfigurationError, ErrorCode


def test_configuration_error_preserves_stable_safe_context() -> None:
    error = ConfigurationError(
        ErrorCode.CONFIG_INVALID,
        "bundle rule is invalid",
        task_name="LiveContainer",
        bundle_id="com.kdt.livecontainer",
        remediation="configure one explicit target",
        safe_details=(("field", "target_bundle_id"),),
    )

    diagnostic = error.to_diagnostic()

    assert str(error) == "bundle rule is invalid"
    assert diagnostic.code == "config.invalid"
    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.task_name == "LiveContainer"
    assert diagnostic.bundle_id == "com.kdt.livecontainer"
    assert diagnostic.details == (("field", "target_bundle_id"),)


def test_adapter_error_adds_adapter_and_operation_details() -> None:
    error = AdapterError(
        ErrorCode.ADAPTER_COMMAND_FAILED,
        "signing command failed",
        adapter="zsign",
        operation="sign",
        safe_details=(("exit_code", 2),),
    )

    assert error.adapter == "zsign"
    assert error.operation == "sign"
    assert error.to_diagnostic().details == (
        ("adapter", "zsign"),
        ("operation", "sign"),
        ("exit_code", 2),
    )
