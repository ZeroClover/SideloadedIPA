"""Pure entitlement-policy materialization and deterministic hashing."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from sideloadedipa.domain.common import (
    FrozenJsonValue,
    canonical_json_bytes,
    freeze_json,
    is_string_sequence,
)
from sideloadedipa.domain.config import EntitlementMode, EntitlementPolicy
from sideloadedipa.domain.entitlement_keys import APPLICATION_GROUPS as _APP_GROUPS
from sideloadedipa.domain.entitlement_keys import APPLICATION_IDENTIFIER as _APPLICATION_IDENTIFIER
from sideloadedipa.domain.entitlement_keys import KEYCHAIN_ACCESS_GROUPS as _KEYCHAIN_GROUPS
from sideloadedipa.domain.entitlement_keys import TEAM_IDENTIFIER as _TEAM_IDENTIFIER
from sideloadedipa.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class EntitlementContext:
    team_id: str
    app_identifier_prefix: str
    source_bundle_id: str
    target_bundle_id: str
    app_group_rewrites: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EntitlementTransformation:
    key: str
    before: FrozenJsonValue
    after: FrozenJsonValue


@dataclass(frozen=True, slots=True)
class MaterializedEntitlements:
    values: tuple[tuple[str, FrozenJsonValue], ...]
    sha256: str
    transformations: tuple[EntitlementTransformation, ...] = ()
    dropped_keys: tuple[str, ...] = ()


def _policy_error(message: str, field: str) -> DomainError:
    return DomainError(
        ErrorCode.ENTITLEMENTS_POLICY_INVALID,
        message,
        remediation="correct the entitlement policy before signing",
        safe_details=(("field", field),),
    )


def _canonical_value(value: object, field: str) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise _policy_error("entitlement dictionary keys must be strings", field)
            result[key] = _canonical_value(child, f"{field}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(child, field) for child in value]
    raise _policy_error("entitlement value has an unsupported type", field)


def _freeze(value: object, field: str) -> FrozenJsonValue:
    # freeze_json coerces non-string mapping keys, so keep the policy-layer
    # key check here before delegating to the shared freeze primitive.
    if isinstance(value, Mapping) and any(not isinstance(key, str) for key in value):
        raise _policy_error("entitlement dictionary keys must be strings", field)
    try:
        return freeze_json(value)
    except TypeError as error:
        raise _policy_error("entitlement value has an unsupported type", field) from error


def _string_array(value: object, field: str) -> list[str]:
    if not is_string_sequence(value):
        raise _policy_error(f"{field} must be an array of strings", field)
    return list(value)


def _source_prefix(source: Mapping[str, object], context: EntitlementContext) -> str | None:
    application_identifier = source.get(_APPLICATION_IDENTIFIER)
    if application_identifier is None:
        return None
    if not isinstance(application_identifier, str) or not application_identifier.endswith(
        context.source_bundle_id
    ):
        raise _policy_error(
            "source application-identifier does not end with the source bundle identifier",
            _APPLICATION_IDENTIFIER,
        )
    return application_identifier[: -len(context.source_bundle_id)]


def _preserve_source(
    source: Mapping[str, object], context: EntitlementContext
) -> tuple[dict[str, object], tuple[EntitlementTransformation, ...]]:
    result = dict(source)
    source_prefix = _source_prefix(source, context)
    target_prefix = context.app_identifier_prefix
    transformations: list[EntitlementTransformation] = []

    replacements: dict[str, object] = {}
    if _APPLICATION_IDENTIFIER in source:
        replacements[_APPLICATION_IDENTIFIER] = f"{target_prefix}{context.target_bundle_id}"
    if _TEAM_IDENTIFIER in source:
        replacements[_TEAM_IDENTIFIER] = context.team_id
    if _KEYCHAIN_GROUPS in source:
        groups = _string_array(source[_KEYCHAIN_GROUPS], _KEYCHAIN_GROUPS)
        replacements[_KEYCHAIN_GROUPS] = [
            (
                f"{target_prefix}{group[len(source_prefix):]}"
                if source_prefix is not None and group.startswith(source_prefix)
                else group
            )
            for group in groups
        ]
    if _APP_GROUPS in source:
        rewrites = dict(context.app_group_rewrites)
        groups = _string_array(source[_APP_GROUPS], _APP_GROUPS)
        replacements[_APP_GROUPS] = [rewrites.get(group, group) for group in groups]

    for key, after in replacements.items():
        before = source[key]
        result[key] = after
        if before != after:
            transformations.append(
                EntitlementTransformation(
                    key=key,
                    before=_freeze(before, key),
                    after=_freeze(after, key),
                )
            )
    return result, tuple(transformations)


def _document_for_policy(
    policy: EntitlementPolicy,
    source: Mapping[str, object],
    context: EntitlementContext,
    profile: Mapping[str, object] | None,
    template: Mapping[str, object] | None,
) -> tuple[Mapping[str, object], tuple[EntitlementTransformation, ...]]:
    if policy.mode is EntitlementMode.PRESERVE_SOURCE:
        return _preserve_source(source, context)
    if policy.mode is EntitlementMode.PROFILE:
        if profile is None:
            raise _policy_error("profile mode requires profile entitlements", "profile")
        return profile, ()
    if template is None:
        raise _policy_error("template mode requires loaded template entitlements", "template")
    return template, ()


def normalize_entitlements(document: Mapping[str, object]) -> MaterializedEntitlements:
    """Validate and freeze one entitlement dictionary with a canonical digest."""

    canonical = cast(dict[str, object], _canonical_value(document, "entitlements"))
    try:
        serialized = canonical_json_bytes(canonical)
    except ValueError as error:
        raise _policy_error(
            "entitlement values must use finite JSON numbers", "entitlements"
        ) from error
    return MaterializedEntitlements(
        values=tuple((key, _freeze(canonical[key], key)) for key in sorted(canonical)),
        sha256=hashlib.sha256(serialized).hexdigest(),
    )


def materialize_entitlements(
    policy: EntitlementPolicy,
    source_entitlements: Mapping[str, object],
    context: EntitlementContext,
    *,
    profile_entitlements: Mapping[str, object] | None = None,
    template_entitlements: Mapping[str, object] | None = None,
) -> MaterializedEntitlements:
    """Apply one entitlement policy and return a deterministic expected document."""

    if policy.allowed_drops and not policy.drop_rationale:
        raise _policy_error("allowed entitlement drops require a rationale", "drop_rationale")
    document, transformations = _document_for_policy(
        policy,
        source_entitlements,
        context,
        profile_entitlements,
        template_entitlements,
    )
    normalized = normalize_entitlements(document)

    dropped = tuple(sorted(set(source_entitlements) - {key for key, _ in normalized.values}))
    undeclared = tuple(key for key in dropped if key not in policy.allowed_drops)
    if undeclared:
        raise DomainError(
            ErrorCode.ENTITLEMENTS_UNDECLARED_DROP,
            "entitlement policy removes undeclared source entitlements",
            remediation="preserve the values or declare allowed drops with a rationale",
            safe_details=(("keys", undeclared),),
        )

    return MaterializedEntitlements(
        values=normalized.values,
        sha256=normalized.sha256,
        transformations=transformations,
        dropped_keys=dropped,
    )
