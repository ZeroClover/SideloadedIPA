"""Shared pytest fixtures for test suite."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Generator, List
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary work directory structure."""
    cache_dir = tmp_path / "work" / "cache"
    cache_old_dir = tmp_path / "work" / "cache-old"
    cache_dir.mkdir(parents=True)
    cache_old_dir.mkdir(parents=True)
    yield tmp_path


@pytest.fixture
def sample_device_list() -> List[Dict[str, Any]]:
    """Sample device list for testing."""
    return [
        {
            "id": "DEVICE001",
            "name": "iPhone 15 Pro",
            "platform": "IOS",
            "device_class": "IPHONE",
            "udid": "00000000-0000-0000-0000-000000000001",
            "status": "ENABLED",
        },
        {
            "id": "DEVICE002",
            "name": "iPad Pro",
            "platform": "IOS",
            "device_class": "IPAD",
            "udid": "00000000-0000-0000-0000-000000000002",
            "status": "ENABLED",
        },
    ]


@pytest.fixture
def sample_device_list_json(sample_device_list: List[Dict]) -> Dict[str, Any]:
    """Sample device list JSON structure with checksum."""
    return {
        "devices": sample_device_list,
        "last_updated": "2025-01-06T12:00:00Z",
        "checksum": "sha256:abc123",
    }


@pytest.fixture
def sample_tasks_toml(tmp_path: Path) -> Path:
    """Create a sample tasks.toml file."""
    toml_content = """
[[tasks]]
task_name = "TestApp"
app_name = "Test App"
bundle_id = "com.example.testapp"
repo_url = "https://github.com/example/testapp"
asset_server_path = "/var/www/assets/"

[[tasks]]
task_name = "DirectURLApp"
app_name = "Direct URL App"
bundle_id = "com.example.directurl"
ipa_url = "https://example.com/app.ipa"
asset_server_path = "/var/www/assets/direct.ipa"
"""
    toml_path = tmp_path / "configs" / "tasks.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    toml_path.write_text(toml_content)
    return toml_path


@pytest.fixture
def sample_release_cache() -> Dict[str, Any]:
    """Sample release version cache."""
    return {
        "tasks": {
            "TestApp": {
                "version": "v1.0.0",
                "published_at": "2025-01-01T00:00:00Z",
                "download_url": "https://github.com/example/testapp/releases/download/v1.0.0/app.ipa",
                "asset_id": 12345,
            }
        },
        "last_updated": "2025-01-06T12:00:00Z",
    }


@pytest.fixture
def mock_github_release() -> Dict[str, Any]:
    """Sample GitHub release API response."""
    return {
        "tag_name": "v1.0.0",
        "name": "Release v1.0.0",
        "published_at": "2025-01-01T00:00:00Z",
        "prerelease": False,
        "assets": [
            {
                "id": 12345,
                "name": "app.ipa",
                "browser_download_url": "https://github.com/example/testapp/releases/download/v1.0.0/app.ipa",
                "size": 10485760,
            },
            {
                "id": 12346,
                "name": "app-debug.ipa",
                "browser_download_url": "https://github.com/example/testapp/releases/download/v1.0.0/app-debug.ipa",
                "size": 15728640,
            },
        ],
    }


@pytest.fixture
def mock_github_prerelease() -> Dict[str, Any]:
    """Sample GitHub prerelease API response."""
    return {
        "tag_name": "v2.0.0-beta.1",
        "name": "Beta Release v2.0.0-beta.1",
        "published_at": "2025-01-05T00:00:00Z",
        "prerelease": True,
        "assets": [
            {
                "id": 12350,
                "name": "app-beta.ipa",
                "browser_download_url": "https://github.com/example/testapp/releases/download/v2.0.0-beta.1/app-beta.ipa",
                "size": 10485760,
            },
        ],
    }


@pytest.fixture
def env_with_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set GITHUB_TOKEN environment variable."""
    monkeypatch.setenv("GITHUB_TOKEN", "test_token_12345")


@pytest.fixture
def env_without_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure GITHUB_TOKEN is not set."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
