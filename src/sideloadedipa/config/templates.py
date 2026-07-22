"""Restricted loading and expansion of entitlement plist templates."""

from __future__ import annotations

import plistlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast

from sideloadedipa.errors import ConfigurationError, ErrorCode

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


@dataclass(frozen=True, slots=True)
class EntitlementTemplateContext:
    team_id: str
    app_identifier_prefix: str
    target_bundle_id: str
    app_groups: tuple[tuple[str, str], ...] = ()


def _fail(code: ErrorCode, message: str, field: str) -> NoReturn:
    raise ConfigurationError(
        code,
        message,
        remediation="use a repository-controlled plist and supported typed placeholders",
        safe_details=(("field", field),),
    )


def _replacement(token: str, context: EntitlementTemplateContext, field: str) -> str:
    fixed = {
        "TEAM_ID": context.team_id,
        "APP_IDENTIFIER_PREFIX": context.app_identifier_prefix,
        "TARGET_BUNDLE_ID": context.target_bundle_id,
    }
    if token in fixed:
        return fixed[token]
    if token.startswith("APP_GROUP:"):
        alias = token.removeprefix("APP_GROUP:")
        groups = dict(context.app_groups)
        if alias in groups:
            return groups[alias]
    _fail(
        ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID,
        f"unknown entitlement template placeholder: {token}",
        field,
    )


def _expand(value: object, context: EntitlementTemplateContext, field: str) -> object:
    if isinstance(value, str):
        expanded = _PLACEHOLDER.sub(
            lambda match: _replacement(match.group(1), context, field), value
        )
        if "${" in expanded:
            _fail(
                ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID,
                "malformed entitlement template placeholder",
                field,
            )
        return expanded
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str) or "${" in key:
                _fail(
                    ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID,
                    "entitlement template keys must be static strings",
                    field,
                )
            result[key] = _expand(child, context, f"{field}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_expand(child, context, field) for child in value]
    _fail(
        ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID,
        "entitlement template contains an unsupported value type",
        field,
    )


def load_entitlement_template(
    repository_root: Path,
    template_path: PurePosixPath,
    context: EntitlementTemplateContext,
) -> dict[str, object]:
    """Load a plist below ``configs/signing`` and expand allowlisted placeholders."""

    root = repository_root.resolve()
    allowed_root = (root / "configs" / "signing").resolve()
    path = Path(template_path)
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    if candidate == allowed_root or not candidate.is_relative_to(allowed_root):
        _fail(
            ErrorCode.ENTITLEMENTS_TEMPLATE_PATH,
            "entitlement template path must stay below configs/signing",
            "entitlements_file",
        )

    try:
        with candidate.open("rb") as handle:
            document = cast(object, plistlib.load(handle))
    except FileNotFoundError as error:
        raise ConfigurationError(
            ErrorCode.ENTITLEMENTS_TEMPLATE_MISSING,
            "entitlement template does not exist",
            remediation="add the reviewed template to configs/signing",
            safe_details=(("path", template_path.as_posix()),),
        ) from error
    except (OSError, plistlib.InvalidFileException) as error:
        raise ConfigurationError(
            ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID,
            "entitlement template could not be decoded",
            safe_details=(("path", template_path.as_posix()),),
        ) from error

    if not isinstance(document, Mapping):
        _fail(
            ErrorCode.ENTITLEMENTS_TEMPLATE_INVALID,
            "entitlement template root must be a dictionary",
            "entitlements_file",
        )
    expanded = _expand(document, context, "entitlements")
    return cast(dict[str, object], expanded)
