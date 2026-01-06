"""Tests for scripts/run_signing.py - GitHub API integration."""

import json
import sys
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from run_signing import (
    GitHubAPIClient,
    load_release_cache,
    parse_repo_url,
    save_release_cache,
    should_rebuild_task,
    validate_task,
)


class TestGitHubAPIClient:
    """Tests for GitHubAPIClient class."""

    def test_init_with_token(self, env_with_github_token: None) -> None:
        """Should initialize successfully with GITHUB_TOKEN set."""
        client = GitHubAPIClient()
        assert client.token == "test_token_12345"

    def test_init_without_token(self, env_without_github_token: None) -> None:
        """Should raise ValueError when GITHUB_TOKEN is not set."""
        with pytest.raises(ValueError, match="GITHUB_TOKEN environment variable is required"):
            GitHubAPIClient()

    def test_fetch_latest_stable_release(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should fetch latest stable release from /releases/latest endpoint."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(mock_github_release).encode()
            mock_response.headers = {
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "1704067200",
            }
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            release = client.fetch_latest_release("example", "testapp", use_prerelease=False)

            assert release is not None
            assert release["tag_name"] == "v1.0.0"
            assert release["prerelease"] is False

            # Verify correct endpoint was called
            call_args = mock_urlopen.call_args[0][0]
            assert "/releases/latest" in call_args.full_url

    def test_fetch_latest_prerelease(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
        mock_github_prerelease: Dict[str, Any],
    ) -> None:
        """Should fetch latest prerelease from /releases endpoint."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            # Return list of releases with prerelease first
            releases_list = [mock_github_prerelease, mock_github_release]

            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(releases_list).encode()
            mock_response.headers = {
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "1704067200",
            }
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            release = client.fetch_latest_release("example", "testapp", use_prerelease=True)

            assert release is not None
            assert release["tag_name"] == "v2.0.0-beta.1"
            assert release["prerelease"] is True

    def test_fetch_prerelease_fallback_to_stable(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should fallback to stable release if no prerelease exists."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            # Return list with only stable releases
            mock_github_release["prerelease"] = False
            releases_list = [mock_github_release]

            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(releases_list).encode()
            mock_response.headers = {}
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            release = client.fetch_latest_release("example", "testapp", use_prerelease=True)

            assert release is not None
            assert release["tag_name"] == "v1.0.0"

    def test_fetch_release_404(self, env_with_github_token: None) -> None:
        """Should return None when no release found (404)."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError(
                url="https://api.github.com/repos/example/testapp/releases/latest",
                code=404,
                msg="Not Found",
                hdrs=MagicMock(),
                fp=BytesIO(b""),
            )

            release = client.fetch_latest_release("example", "testapp", use_prerelease=False)
            assert release is None

    def test_find_matching_asset_single_match(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should find single matching asset by glob pattern."""
        client = GitHubAPIClient()

        asset = client.find_matching_asset(mock_github_release, "*.ipa")

        assert asset is not None
        assert asset["name"] == "app.ipa"

    def test_find_matching_asset_specific_pattern(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should match specific pattern."""
        client = GitHubAPIClient()

        asset = client.find_matching_asset(mock_github_release, "*-debug.ipa")

        assert asset is not None
        assert asset["name"] == "app-debug.ipa"

    def test_find_matching_asset_no_match(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should return None when no assets match pattern."""
        client = GitHubAPIClient()

        asset = client.find_matching_asset(mock_github_release, "*.apk")
        assert asset is None

    def test_find_matching_asset_empty_assets(
        self,
        env_with_github_token: None,
    ) -> None:
        """Should return None when release has no assets."""
        client = GitHubAPIClient()
        release = {"tag_name": "v1.0.0", "assets": []}

        asset = client.find_matching_asset(release, "*.ipa")
        assert asset is None

    def test_find_matching_asset_multiple_matches(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should return first match and warn when multiple assets match."""
        client = GitHubAPIClient()

        # Both assets match *.ipa pattern
        asset = client.find_matching_asset(mock_github_release, "*.ipa")

        assert asset is not None
        # Should return first match
        assert asset["name"] == "app.ipa"


class TestRateLimitHandling:
    """Tests for GitHub API rate limit handling."""

    def test_rate_limit_warning_when_low(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Should warn when rate limit is low (<100)."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(mock_github_release).encode()
            mock_response.headers = {
                "X-RateLimit-Remaining": "50",
                "X-RateLimit-Reset": "1704067200",
            }
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            client.fetch_latest_release("example", "testapp")

            captured = capsys.readouterr()
            assert "rate limit low" in captured.out.lower()

    def test_rate_limit_exceeded_error(
        self,
        env_with_github_token: None,
    ) -> None:
        """Should raise HTTPError when rate limit exceeded (403)."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            error = HTTPError(
                url="https://api.github.com/repos/example/testapp/releases/latest",
                code=403,
                msg="Forbidden",
                hdrs=MagicMock(get=lambda x: "1704067200" if x == "X-RateLimit-Reset" else None),
                fp=BytesIO(b""),
            )
            mock_urlopen.side_effect = error

            with pytest.raises(HTTPError) as exc_info:
                client._make_request("https://api.github.com/repos/example/testapp/releases/latest")

            assert exc_info.value.code == 403


class TestAuthenticationHeader:
    """Tests for GitHub API authentication header inclusion."""

    def test_bearer_token_in_header(self, env_with_github_token: None) -> None:
        """Should include Bearer token in Authorization header."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b'{"tag_name": "v1.0.0"}'
            mock_response.headers = {}
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            client._make_request("https://api.github.com/test")

            # Verify Authorization header was set
            call_args = mock_urlopen.call_args[0][0]
            auth_header = call_args.get_header("Authorization")
            assert auth_header == "Bearer test_token_12345"

    def test_github_api_version_header(self, env_with_github_token: None) -> None:
        """Should include GitHub API version header."""
        client = GitHubAPIClient()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b'{"tag_name": "v1.0.0"}'
            mock_response.headers = {}
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            client._make_request("https://api.github.com/test")

            call_args = mock_urlopen.call_args[0][0]
            version_header = call_args.get_header("X-github-api-version")
            assert version_header == "2022-11-28"


class TestParseRepoUrl:
    """Tests for GitHub repository URL parsing."""

    def test_https_url(self) -> None:
        """Should parse HTTPS GitHub URL."""
        owner, repo = parse_repo_url("https://github.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_https_url_with_git_suffix(self) -> None:
        """Should parse HTTPS URL with .git suffix."""
        owner, repo = parse_repo_url("https://github.com/owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"

    def test_ssh_url(self) -> None:
        """Should parse SSH GitHub URL."""
        owner, repo = parse_repo_url("git@github.com:owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_invalid_url(self) -> None:
        """Should raise ValueError for invalid URL."""
        with pytest.raises(ValueError, match="Invalid GitHub repository URL"):
            parse_repo_url("https://gitlab.com/owner/repo")


class TestValidateTask:
    """Tests for task configuration validation."""

    def test_valid_repo_url_task(self) -> None:
        """Should validate task with repo_url."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "repo_url": "https://github.com/example/testapp",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is True
        assert error is None

    def test_valid_ipa_url_task(self) -> None:
        """Should validate task with ipa_url."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "ipa_url": "https://example.com/app.ipa",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is True
        assert error is None

    def test_missing_required_field(self) -> None:
        """Should fail validation for missing required field."""
        task = {
            "task_name": "TestApp",
            # Missing app_name
            "bundle_id": "com.example.testapp",
            "repo_url": "https://github.com/example/testapp",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is False
        assert "app_name" in error

    def test_mutual_exclusion_both_present(self) -> None:
        """Should fail validation when both ipa_url and repo_url are present."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "ipa_url": "https://example.com/app.ipa",
            "repo_url": "https://github.com/example/testapp",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is False
        assert "mutually exclusive" in error

    def test_mutual_exclusion_neither_present(self) -> None:
        """Should fail validation when neither ipa_url nor repo_url is present."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is False
        assert "ipa_url" in error and "repo_url" in error

    def test_invalid_ipa_url_format(self) -> None:
        """Should fail validation for non-HTTP ipa_url."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "ipa_url": "ftp://example.com/app.ipa",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is False
        assert "HTTP" in error

    def test_invalid_repo_url_format(self) -> None:
        """Should fail validation for invalid GitHub repo URL."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "repo_url": "https://gitlab.com/example/testapp",
            "asset_server_path": "/var/www/",
        }
        is_valid, error = validate_task(task)
        assert is_valid is False
        assert "Invalid GitHub" in error


class TestReleaseCacheOperations:
    """Tests for release version cache loading and saving."""

    def test_load_existing_cache(self, tmp_path: Path, sample_release_cache: Dict) -> None:
        """Should load existing cache file."""
        cache_path = tmp_path / "release-versions.json"
        cache_path.write_text(json.dumps(sample_release_cache))

        cache = load_release_cache(cache_path)

        assert cache["tasks"]["TestApp"]["version"] == "v1.0.0"
        assert cache["last_updated"] == "2025-01-06T12:00:00Z"

    def test_load_nonexistent_cache(self, tmp_path: Path) -> None:
        """Should return empty cache structure for non-existent file."""
        cache_path = tmp_path / "nonexistent.json"

        cache = load_release_cache(cache_path)

        assert cache == {"tasks": {}, "last_updated": None}

    def test_load_invalid_cache(self, tmp_path: Path) -> None:
        """Should return empty cache structure for invalid JSON."""
        cache_path = tmp_path / "invalid.json"
        cache_path.write_text("not valid json")

        cache = load_release_cache(cache_path)

        assert cache == {"tasks": {}, "last_updated": None}

    def test_save_cache(self, tmp_path: Path) -> None:
        """Should save cache to file."""
        cache_path = tmp_path / "release-versions.json"
        cache_data = {
            "tasks": {
                "TestApp": {
                    "version": "v2.0.0",
                    "published_at": "2025-01-06T12:00:00Z",
                }
            }
        }

        save_release_cache(cache_path, cache_data)

        assert cache_path.exists()
        saved_data = json.loads(cache_path.read_text())
        assert saved_data["tasks"]["TestApp"]["version"] == "v2.0.0"
        assert "last_updated" in saved_data

    def test_save_cache_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Should create parent directories if they don't exist."""
        cache_path = tmp_path / "nested" / "dir" / "cache.json"
        cache_data = {"tasks": {}}

        save_release_cache(cache_path, cache_data)

        assert cache_path.exists()


class TestShouldRebuildTask:
    """Tests for task rebuild decision logic."""

    def test_ipa_url_always_rebuilds(self) -> None:
        """Tasks with ipa_url should always rebuild."""
        task = {
            "task_name": "DirectApp",
            "ipa_url": "https://example.com/app.ipa",
        }
        cache_data = {"tasks": {}}

        should_rebuild, url, version_info = should_rebuild_task(
            task, "DirectApp", cache_data, github_client=None
        )

        assert should_rebuild is True
        assert url == "https://example.com/app.ipa"
        assert version_info is None  # No version tracking for direct URLs

    def test_version_change_triggers_rebuild(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should rebuild when version changes."""
        task = {
            "task_name": "TestApp",
            "repo_url": "https://github.com/example/testapp",
        }
        cache_data = {
            "tasks": {
                "TestApp": {
                    "version": "v0.9.0",  # Old version
                    "published_at": "2024-12-01T00:00:00Z",
                }
            }
        }

        client = GitHubAPIClient()

        with patch.object(client, "fetch_latest_release") as mock_fetch:
            mock_fetch.return_value = mock_github_release

            with patch.object(client, "find_matching_asset") as mock_find:
                mock_find.return_value = mock_github_release["assets"][0]

                should_rebuild, url, version_info = should_rebuild_task(
                    task, "TestApp", cache_data, client
                )

        assert should_rebuild is True
        assert version_info is not None
        assert version_info["version"] == "v1.0.0"

    def test_no_version_change_skips_rebuild(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should skip rebuild when version unchanged."""
        task = {
            "task_name": "TestApp",
            "repo_url": "https://github.com/example/testapp",
        }
        cache_data = {
            "tasks": {
                "TestApp": {
                    "version": "v1.0.0",  # Same version
                    "published_at": "2025-01-01T00:00:00Z",  # Same timestamp
                }
            }
        }

        client = GitHubAPIClient()

        with patch.object(client, "fetch_latest_release") as mock_fetch:
            mock_fetch.return_value = mock_github_release

            with patch.object(client, "find_matching_asset") as mock_find:
                mock_find.return_value = mock_github_release["assets"][0]

                should_rebuild, url, version_info = should_rebuild_task(
                    task, "TestApp", cache_data, client
                )

        assert should_rebuild is False
        assert version_info is None  # No update needed

    def test_first_run_triggers_rebuild(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should rebuild on first run (no cache)."""
        task = {
            "task_name": "NewApp",
            "repo_url": "https://github.com/example/newapp",
        }
        cache_data = {"tasks": {}}  # Empty cache

        client = GitHubAPIClient()

        with patch.object(client, "fetch_latest_release") as mock_fetch:
            mock_fetch.return_value = mock_github_release

            with patch.object(client, "find_matching_asset") as mock_find:
                mock_find.return_value = mock_github_release["assets"][0]

                should_rebuild, url, version_info = should_rebuild_task(
                    task, "NewApp", cache_data, client
                )

        assert should_rebuild is True
        assert version_info is not None

    def test_force_rebuild_ignores_cache(
        self,
        env_with_github_token: None,
        mock_github_release: Dict[str, Any],
    ) -> None:
        """Should rebuild when force_rebuild is True regardless of cache."""
        task = {
            "task_name": "TestApp",
            "repo_url": "https://github.com/example/testapp",
        }
        cache_data = {
            "tasks": {
                "TestApp": {
                    "version": "v1.0.0",  # Same version
                    "published_at": "2025-01-01T00:00:00Z",
                }
            }
        }

        client = GitHubAPIClient()

        with patch.object(client, "fetch_latest_release") as mock_fetch:
            mock_fetch.return_value = mock_github_release

            with patch.object(client, "find_matching_asset") as mock_find:
                mock_find.return_value = mock_github_release["assets"][0]

                should_rebuild, url, version_info = should_rebuild_task(
                    task, "TestApp", cache_data, client, force_rebuild=True
                )

        assert should_rebuild is True
        assert version_info is not None
