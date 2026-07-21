"""Source asset selection adapters."""

from sideloadedipa.sources.download import DownloadedSource, download_source_asset
from sideloadedipa.sources.github import GitHubReleaseAsset, select_release_asset

__all__ = [
    "DownloadedSource",
    "GitHubReleaseAsset",
    "download_source_asset",
    "select_release_asset",
]
