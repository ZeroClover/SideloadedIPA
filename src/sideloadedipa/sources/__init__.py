"""Source asset selection adapters."""

from sideloadedipa.sources.download import (
    DEFAULT_DOWNLOAD_POLICY,
    DownloadedSource,
    DownloadPolicy,
    download_source_asset,
)
from sideloadedipa.sources.github import (
    GitHubReleaseAsset,
    fetch_github_release,
    github_repository_name,
    select_release_asset,
)

__all__ = [
    "DEFAULT_DOWNLOAD_POLICY",
    "DownloadedSource",
    "DownloadPolicy",
    "GitHubReleaseAsset",
    "download_source_asset",
    "fetch_github_release",
    "github_repository_name",
    "select_release_asset",
]
