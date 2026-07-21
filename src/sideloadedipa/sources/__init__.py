"""Source asset selection adapters."""

from sideloadedipa.sources.github import GitHubReleaseAsset, select_release_asset

__all__ = ["GitHubReleaseAsset", "select_release_asset"]
