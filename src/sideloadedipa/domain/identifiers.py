"""Pure bundle-identifier validation and mapping policy."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sideloadedipa.errors import DomainError, ErrorCode

_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")


@dataclass(frozen=True, slots=True)
class BundleIdentifierMapping:
    source_bundle_id: str
    target_bundle_id: str


def validate_bundle_identifier(identifier: str, *, field: str) -> str:
    """Return a valid explicit bundle identifier or raise a typed error."""

    if not identifier or not _BUNDLE_ID_PATTERN.fullmatch(identifier) or "*" in identifier:
        raise DomainError(
            ErrorCode.IDENTIFIER_INVALID,
            f"{field} is not a valid explicit bundle identifier",
            bundle_id=identifier or None,
            remediation="use only letters, numbers, hyphens, and periods",
            safe_details=(("field", field),),
        )
    return identifier


def derive_target_bundle_id(
    source_bundle_id: str,
    *,
    source_root_bundle_id: str,
    target_root_bundle_id: str,
    explicit_target_bundle_id: str | None = None,
) -> str:
    """Apply preserve-source-suffix policy to one source identifier."""

    validate_bundle_identifier(source_bundle_id, field="source_bundle_id")
    validate_bundle_identifier(source_root_bundle_id, field="source_root_bundle_id")
    validate_bundle_identifier(target_root_bundle_id, field="target_root_bundle_id")
    if explicit_target_bundle_id is not None:
        return validate_bundle_identifier(
            explicit_target_bundle_id, field="explicit_target_bundle_id"
        )
    if source_bundle_id == source_root_bundle_id:
        return target_root_bundle_id

    descendant_prefix = f"{source_root_bundle_id}."
    if not source_bundle_id.startswith(descendant_prefix):
        raise DomainError(
            ErrorCode.IDENTIFIER_NON_DESCENDANT,
            "a non-descendant source bundle requires an explicit target identifier",
            bundle_id=source_bundle_id,
            remediation="set target_bundle_id on the matching bundle rule",
            safe_details=(("source_root_bundle_id", source_root_bundle_id),),
        )
    return f"{target_root_bundle_id}{source_bundle_id[len(source_root_bundle_id):]}"


def derive_identifier_mappings(
    source_bundle_ids: Iterable[str],
    *,
    source_root_bundle_id: str,
    target_root_bundle_id: str,
    explicit_targets: Mapping[str, str] | None = None,
) -> tuple[BundleIdentifierMapping, ...]:
    """Derive deterministic mappings and reject case-insensitive target collisions."""

    overrides = explicit_targets or {}
    mappings = tuple(
        BundleIdentifierMapping(
            source_bundle_id=source_bundle_id,
            target_bundle_id=derive_target_bundle_id(
                source_bundle_id,
                source_root_bundle_id=source_root_bundle_id,
                target_root_bundle_id=target_root_bundle_id,
                explicit_target_bundle_id=overrides.get(source_bundle_id),
            ),
        )
        for source_bundle_id in sorted(source_bundle_ids)
    )

    sources_by_target: dict[str, list[str]] = {}
    for mapping in mappings:
        sources_by_target.setdefault(mapping.target_bundle_id.casefold(), []).append(
            mapping.source_bundle_id
        )
    collisions = tuple(
        (target, tuple(sources))
        for target, sources in sorted(sources_by_target.items())
        if len(sources) > 1
    )
    if collisions:
        raise DomainError(
            ErrorCode.IDENTIFIER_COLLISION,
            "multiple source bundles resolve to the same target identifier",
            remediation="configure unique target_bundle_id overrides",
            safe_details=(("collisions", collisions),),
        )
    return mappings
