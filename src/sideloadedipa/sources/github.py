"""Deterministic GitHub release asset selection."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass

from sideloadedipa.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class GitHubReleaseAsset:
    index: int
    asset_id: str
    name: str
    browser_download_url: str
    size: int
    digest: str | None = None


def _invalid_release(message: str, field: str) -> DomainError:
    return DomainError(
        ErrorCode.SOURCE_RELEASE_INVALID,
        message,
        remediation="retry with an unmodified GitHub release API response",
        safe_details=(("field", field),),
    )


def select_release_asset(release: Mapping[str, object], glob_pattern: str) -> GitHubReleaseAsset:
    """Select exactly one matching IPA asset and retain source evidence."""

    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise _invalid_release("release assets must be an array", "assets")

    assets: list[tuple[int, Mapping[str, object], str]] = []
    for index, raw_asset in enumerate(raw_assets):
        if not isinstance(raw_asset, Mapping):
            raise _invalid_release("release asset must be an object", f"assets[{index}]")
        name = raw_asset.get("name")
        if not isinstance(name, str) or not name:
            raise _invalid_release(
                "release asset name must be a non-empty string", f"assets[{index}].name"
            )
        assets.append((index, raw_asset, name))

    matches = [asset for asset in assets if fnmatch.fnmatch(asset[2], glob_pattern)]
    if not matches:
        raise DomainError(
            ErrorCode.SOURCE_ASSET_NOT_FOUND,
            "no release asset matches the configured pattern",
            remediation="review available assets and configure one exact release_glob",
            safe_details=(
                ("pattern", glob_pattern),
                ("available_names", tuple(asset[2] for asset in assets)),
            ),
        )
    if len(matches) > 1:
        raise DomainError(
            ErrorCode.SOURCE_ASSET_AMBIGUOUS,
            "multiple release assets match the configured pattern",
            remediation="configure a release_glob that matches exactly one IPA",
            safe_details=(
                ("pattern", glob_pattern),
                ("matching_names", tuple(asset[2] for asset in matches)),
            ),
        )

    index, raw_asset, name = matches[0]
    asset_id = raw_asset.get("id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, (str, int)):
        raise _invalid_release(
            "release asset id must be a string or integer", f"assets[{index}].id"
        )
    url = raw_asset.get("browser_download_url")
    if not isinstance(url, str) or not url:
        raise _invalid_release(
            "release asset download URL must be a non-empty string",
            f"assets[{index}].browser_download_url",
        )
    size = raw_asset.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise _invalid_release(
            "release asset size must be a non-negative integer", f"assets[{index}].size"
        )
    digest = raw_asset.get("digest")
    if digest is not None and not isinstance(digest, str):
        raise _invalid_release(
            "release asset digest must be a string when present", f"assets[{index}].digest"
        )
    return GitHubReleaseAsset(
        index=index,
        asset_id=str(asset_id),
        name=name,
        browser_download_url=url,
        size=size,
        digest=digest,
    )
