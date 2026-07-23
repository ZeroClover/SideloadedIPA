"""Deterministic GitHub release asset selection."""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from sideloadedipa.errors import AdapterError, DomainError, ErrorCode

_API_VERSION = "2026-03-10"
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


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


def github_repository_name(repository_url: str) -> str:
    """Return a safe owner/repository name from a validated GitHub URL."""

    if repository_url.startswith("git@github.com:"):
        path = repository_url.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(repository_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname != "github.com":
            raise _invalid_release("repository URL must use github.com", "repository_url")
        path = parsed.path.lstrip("/")
    parts = path.removesuffix("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(parts):
        raise _invalid_release("repository URL must contain owner and repository", "repository_url")
    return f"{parts[0]}/{parts[1]}"


def _read_json(request: Request, *, timeout_seconds: float) -> object:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError) as error:
        status = error.code if isinstance(error, HTTPError) else None
        details = (("status", status),) if status is not None else ()
        raise AdapterError(
            ErrorCode.ADAPTER_RESPONSE_INVALID,
            "GitHub release request failed",
            adapter="github-rest",
            operation="read-release",
            remediation="retry or verify repository access and GitHub API availability",
            safe_details=details,
        ) from error
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise AdapterError(
            ErrorCode.ADAPTER_RESPONSE_INVALID,
            "GitHub release response exceeds the configured limit",
            adapter="github-rest",
            operation="read-release",
        )
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterError(
            ErrorCode.ADAPTER_RESPONSE_INVALID,
            "GitHub release response is not valid JSON",
            adapter="github-rest",
            operation="decode-release",
        ) from error


def fetch_github_release(
    repository_url: str,
    *,
    use_prerelease: bool = False,
    token: str | None = None,
    timeout_seconds: float = 30,
) -> Mapping[str, object]:
    """Fetch the latest stable release or newest published prerelease."""

    repository = github_repository_name(repository_url)
    encoded_repository = "/".join(quote(part, safe="") for part in repository.split("/"))
    endpoint = f"https://api.github.com/repos/{encoded_repository}/releases"
    endpoint += "?per_page=100" if use_prerelease else "/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SideloadedIPA/1",
        "X-GitHub-Api-Version": _API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    decoded = _read_json(Request(endpoint, headers=headers), timeout_seconds=timeout_seconds)
    selected: object
    if use_prerelease:
        if not isinstance(decoded, list):
            raise _invalid_release("release list must be an array", "release")
        candidates = [
            release
            for release in decoded
            if isinstance(release, Mapping) and release.get("draft") is False
        ]
        selected = next(
            (release for release in candidates if release.get("prerelease") is True),
            candidates[0] if candidates else None,
        )
    else:
        selected = decoded
    if not isinstance(selected, Mapping):
        raise _invalid_release("release response must contain a release object", "release")
    return selected


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
    if digest is not None and (
        not isinstance(digest, str) or _SHA256_DIGEST.fullmatch(digest) is None
    ):
        raise _invalid_release(
            "release asset digest must be a canonical SHA-256 when present",
            f"assets[{index}].digest",
        )
    return GitHubReleaseAsset(
        index=index,
        asset_id=str(asset_id),
        name=name,
        browser_download_url=url,
        size=size,
        digest=digest.lower() if digest is not None else None,
    )
