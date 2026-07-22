"""Typed semantic comparison for entitlement dictionaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sideloadedipa.domain import FrozenJsonValue, freeze_json, thaw_json

_APPLICATION_IDENTIFIER = "application-identifier"
_TEAM_IDENTIFIER = "com.apple.developer.team-identifier"
_KEYCHAIN_GROUPS = "keychain-access-groups"
_APP_GROUPS = "com.apple.security.application-groups"
_DEFAULT_SET_LIKE_KEYS = frozenset({_KEYCHAIN_GROUPS, _APP_GROUPS})
_PROFILE_WILDCARD_KEYS = frozenset({_APPLICATION_IDENTIFIER, _KEYCHAIN_GROUPS})
_MISSING = object()


class EntitlementComparisonMode(StrEnum):
    EXACT = "exact"
    PROFILE_AUTHORIZATION = "profile-authorization"


@dataclass(frozen=True, slots=True)
class EntitlementIdentityContext:
    team_id: str
    app_identifier_prefix: str
    target_bundle_id: str


@dataclass(frozen=True, slots=True)
class EntitlementDifference:
    path: str
    reason: str
    expected_sha256: str | None
    actual_sha256: str | None


@dataclass(frozen=True, slots=True)
class EntitlementComparison:
    differences: tuple[EntitlementDifference, ...]

    @property
    def passed(self) -> bool:
        return not self.differences


def _digest(value: object) -> str | None:
    if value is _MISSING:
        return None
    frozen: FrozenJsonValue = freeze_json(value)
    encoded = json.dumps(
        thaw_json(frozen),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _difference(path: str, reason: str, expected: object, actual: object) -> EntitlementDifference:
    return EntitlementDifference(path, reason, _digest(expected), _digest(actual))


def _is_array(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _wildcard_authorizes(allowed: str, expected: str) -> bool:
    return allowed.count("*") == 1 and allowed.endswith("*") and expected.startswith(allowed[:-1])


def _scalar_authorized(key: str, allowed: object, expected: object) -> bool:
    if type(allowed) is type(expected) and allowed == expected:
        return True
    return (
        key in _PROFILE_WILDCARD_KEYS
        and isinstance(allowed, str)
        and isinstance(expected, str)
        and _wildcard_authorizes(allowed, expected)
    )


def _set_like_comparison(
    key: str,
    expected: Sequence[object],
    actual: Sequence[object],
    mode: EntitlementComparisonMode,
) -> str | None:
    expected_frozen = tuple(freeze_json(value) for value in expected)
    actual_frozen = tuple(freeze_json(value) for value in actual)
    if len(set(expected_frozen)) != len(expected_frozen):
        return "expected-duplicate"
    if len(set(actual_frozen)) != len(actual_frozen):
        return "actual-duplicate"
    if mode is EntitlementComparisonMode.EXACT or key == _APP_GROUPS:
        return None if set(expected_frozen) == set(actual_frozen) else "set-mismatch"
    for expected_value in expected:
        if not any(_scalar_authorized(key, allowed, expected_value) for allowed in actual):
            return "unauthorized-value"
    return None


def _compare_value(
    key: str,
    path: str,
    expected: object,
    actual: object,
    mode: EntitlementComparisonMode,
    set_like_keys: frozenset[str],
) -> EntitlementDifference | None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return _difference(path, "type-mismatch", expected, actual)
        expected_keys = set(expected)
        actual_keys = set(actual)
        required_actual = (
            expected_keys
            if mode is EntitlementComparisonMode.PROFILE_AUTHORIZATION
            else actual_keys
        )
        if expected_keys != required_actual:
            return _difference(path, "keys-mismatch", expected, actual)
        for child_key in sorted(expected_keys):
            if child_key not in actual:
                return _difference(f"{path}.{child_key}", "missing", expected[child_key], _MISSING)
            difference = _compare_value(
                key,
                f"{path}.{child_key}",
                expected[child_key],
                actual[child_key],
                mode,
                set_like_keys,
            )
            if difference is not None:
                return difference
        return None
    if _is_array(expected):
        if not _is_array(actual):
            return _difference(path, "type-mismatch", expected, actual)
        assert isinstance(expected, Sequence) and isinstance(actual, Sequence)
        if key in set_like_keys:
            reason = _set_like_comparison(key, expected, actual, mode)
            return _difference(path, reason, expected, actual) if reason is not None else None
        if mode is EntitlementComparisonMode.PROFILE_AUTHORIZATION:
            actual_values = tuple(freeze_json(value) for value in actual)
            if all(freeze_json(value) in actual_values for value in expected):
                return None
            return _difference(path, "unauthorized-value", expected, actual)
        if len(expected) != len(actual):
            return _difference(path, "ordered-length-mismatch", expected, actual)
        for index, (expected_value, actual_value) in enumerate(zip(expected, actual)):
            difference = _compare_value(
                key,
                f"{path}[{index}]",
                expected_value,
                actual_value,
                mode,
                set_like_keys,
            )
            if difference is not None:
                return difference
        return None
    passed = (
        _scalar_authorized(key, actual, expected)
        if mode is EntitlementComparisonMode.PROFILE_AUTHORIZATION
        else type(actual) is type(expected) and actual == expected
    )
    return None if passed else _difference(path, "value-mismatch", expected, actual)


def _identity_differences(
    actual: Mapping[str, object],
    context: EntitlementIdentityContext,
) -> tuple[EntitlementDifference, ...]:
    differences: list[EntitlementDifference] = []
    expected_application = f"{context.app_identifier_prefix}{context.target_bundle_id}"
    application = actual.get(_APPLICATION_IDENTIFIER, _MISSING)
    if application != expected_application:
        differences.append(
            _difference(
                _APPLICATION_IDENTIFIER,
                "target-identifier-mismatch",
                expected_application,
                application,
            )
        )
    team = actual.get(_TEAM_IDENTIFIER, _MISSING)
    if team is not _MISSING and team != context.team_id:
        differences.append(_difference(_TEAM_IDENTIFIER, "team-mismatch", context.team_id, team))
    return tuple(differences)


def compare_entitlements(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    *,
    mode: EntitlementComparisonMode = EntitlementComparisonMode.EXACT,
    identity: EntitlementIdentityContext | None = None,
    set_like_keys: frozenset[str] = _DEFAULT_SET_LIKE_KEYS,
) -> EntitlementComparison:
    """Compare typed values with explicit exact or profile-authorization semantics."""

    differences: list[EntitlementDifference] = []
    expected_keys = set(expected)
    actual_keys = set(actual)
    for key in sorted(expected_keys - actual_keys):
        differences.append(_difference(key, "missing", expected[key], _MISSING))
    if mode is EntitlementComparisonMode.EXACT:
        for key in sorted(actual_keys - expected_keys):
            differences.append(_difference(key, "unexpected", _MISSING, actual[key]))
    for key in sorted(expected_keys & actual_keys):
        difference = _compare_value(key, key, expected[key], actual[key], mode, set_like_keys)
        if difference is not None:
            differences.append(difference)
    if identity is not None and mode is EntitlementComparisonMode.EXACT:
        differences.extend(_identity_differences(actual, identity))
    return EntitlementComparison(tuple(differences))
