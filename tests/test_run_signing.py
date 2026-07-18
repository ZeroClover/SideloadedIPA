"""Tests for scripts/run_signing.py - GitHub API integration."""

import json
import plistlib
import sys
import zipfile
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
    build_p12_normalize_commands,
    build_zsign_argv,
    extract_ipa_metadata,
    find_zsign,
    load_release_cache,
    parse_repo_url,
    publish_registry,
    save_release_cache,
    should_rebuild_task,
    task_slug,
    trigger_revalidate,
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
        }
        is_valid, error = validate_task(task)
        assert is_valid is True
        assert error is None

    def test_valid_explicit_slug(self) -> None:
        """An explicit slug keeps legacy directory names (e.g. fehviewer)."""
        task = {
            "task_name": "Eros FE",
            "app_name": "Eros FE",
            "bundle_id": "io.zeroclover.apps.eros-fe",
            "repo_url": "https://github.com/erosTeam/eros_fe",
            "slug": "fehviewer",
        }
        is_valid, error = validate_task(task)
        assert is_valid is True
        assert error is None

    def test_invalid_slug_rejected(self) -> None:
        """Slugs become object-key path segments, so no slashes / spaces."""
        task = {
            "task_name": "TestApp",
            "app_name": "Test App",
            "bundle_id": "com.example.testapp",
            "repo_url": "https://github.com/example/testapp",
            "slug": "bad/slug",
        }
        is_valid, error = validate_task(task)
        assert is_valid is False
        assert "slug" in error

    def test_missing_required_field(self) -> None:
        """Should fail validation for missing required field."""
        task = {
            "task_name": "TestApp",
            # Missing app_name
            "bundle_id": "com.example.testapp",
            "repo_url": "https://github.com/example/testapp",
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


def _make_ipa(
    tmp_path: Path,
    app_name: str = "Demo",
    bundle_id: str = "io.zeroclover.app.demo",
    short_version: str = "1.2.3",
    build_version: str = "123",
    nested_ext: bool = True,
    fmt: int = plistlib.FMT_BINARY,
) -> Path:
    """Build a minimal IPA whose .app Info.plist carries the given metadata."""
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": short_version,
        "CFBundleVersion": build_version,
    }
    ipa_path = tmp_path / f"{app_name}.ipa"
    with zipfile.ZipFile(ipa_path, "w") as zf:
        zf.writestr(f"Payload/{app_name}.app/Info.plist", plistlib.dumps(info, fmt=fmt))
        if nested_ext:
            # A nested app extension with DIFFERENT metadata that must be ignored.
            ext_info = {
                "CFBundleIdentifier": f"{bundle_id}.ext",
                "CFBundleShortVersionString": "9.9.9",
            }
            zf.writestr(
                f"Payload/{app_name}.app/PlugIns/Helper.appex/Info.plist",
                plistlib.dumps(ext_info, fmt=fmt),
            )
    return ipa_path


class TestExtractIpaMetadata:
    """Tests for reading bundle id / version from a signed IPA."""

    def test_reads_short_version(self, tmp_path: Path) -> None:
        ipa = _make_ipa(tmp_path, short_version="7.4.11", build_version="741100")
        bundle_id, version = extract_ipa_metadata(ipa)
        assert bundle_id == "io.zeroclover.app.demo"
        assert version == "7.4.11"

    def test_ignores_nested_extension(self, tmp_path: Path) -> None:
        """The top-level .app Info.plist wins over nested .appex plists."""
        ipa = _make_ipa(tmp_path, bundle_id="io.zeroclover.app.demo")
        bundle_id, _ = extract_ipa_metadata(ipa)
        assert bundle_id == "io.zeroclover.app.demo"  # not ...demo.ext

    def test_falls_back_to_build_version(self, tmp_path: Path) -> None:
        # IPA without CFBundleShortVersionString
        info = {"CFBundleIdentifier": "io.zeroclover.app.demo", "CFBundleVersion": "42"}
        ipa = tmp_path / "Demo.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            zf.writestr("Payload/Demo.app/Info.plist", plistlib.dumps(info))
        _, version = extract_ipa_metadata(ipa)
        assert version == "42"

    def test_xml_plist_supported(self, tmp_path: Path) -> None:
        ipa = _make_ipa(tmp_path, fmt=plistlib.FMT_XML)
        bundle_id, version = extract_ipa_metadata(ipa)
        assert bundle_id == "io.zeroclover.app.demo"
        assert version == "1.2.3"

    def test_no_info_plist_raises(self, tmp_path: Path) -> None:
        ipa = tmp_path / "empty.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            zf.writestr("Payload/README.txt", "no app here")
        with pytest.raises(ValueError, match="No app Info.plist"):
            extract_ipa_metadata(ipa)


class TestTaskSlug:
    """Tests for resolving a task's stable R2/registry slug."""

    def test_explicit_slug_wins(self) -> None:
        assert task_slug({"app_name": "Eros FE", "slug": "fehviewer"}) == "fehviewer"

    def test_defaults_to_slugified_app_name(self) -> None:
        assert task_slug({"app_name": "JHenTai"}) == "JHenTai"
        assert task_slug({"app_name": "Eros FE"}) == "Eros_FE"


class TestTriggerRevalidate:
    """Tests for the Vercel on-demand revalidation hook call."""

    def test_missing_secret_skips(self, capsys: pytest.CaptureFixture) -> None:
        assert trigger_revalidate("https://example.com/api/revalidate", "") is False
        assert "VERCEL_REVALIDATE_SECRET" in capsys.readouterr().err

    def test_appends_secret_query(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock(status=200)
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert trigger_revalidate("https://example.com/api/revalidate", "s3cr3t") is True

        called_url = mock_urlopen.call_args[0][0].full_url
        assert called_url == "https://example.com/api/revalidate?secret=s3cr3t"

    def test_http_failure_returns_false(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError("connection refused")
            assert trigger_revalidate("https://example.com/api/revalidate", "s3cr3t") is False


class TestPublishRegistry:
    """Tests for the apps.json publish chain: merge -> upload -> revalidate -> cleanup."""

    def _store(self, current_doc: Dict[str, Any] | None) -> MagicMock:
        store = MagicMock()
        store.apps_json_key = "site/apps.json"
        store.download_json.return_value = current_doc
        return store

    def _update(self) -> Dict[str, Any]:
        return {
            "slug": "ehpanda",
            "name": "EhPanda",
            "bundleId": "io.zeroclover.app.ehpanda",
            "version": "2.7.4",
            "ipaUrl": "https://ipa.zeroclover.io/apps/ehpanda/2.7.4/EhPanda.ipa",
            "iconUrl": "https://ipa.zeroclover.io/apps/ehpanda/icon.png",
        }

    def test_changed_doc_uploads_revalidates_and_cleans(self) -> None:
        store = self._store(None)  # bootstrap: apps.json missing on R2
        with patch("run_signing.trigger_revalidate", return_value=True) as mock_revalidate:
            ok = publish_registry(store, [self._update()], ["ehpanda"], "https://x/revalidate", "sec")
        assert ok is True
        store.upload_json.assert_called_once()
        mock_revalidate.assert_called_once_with("https://x/revalidate", "sec")
        store.cleanup_stale.assert_called_once()
        slugs_arg = store.cleanup_stale.call_args[0][0]
        assert slugs_arg == ["ehpanda"]

    def test_unchanged_doc_skips_upload_but_still_cleans(self) -> None:
        current = {
            "updatedAt": "2026-07-18T04:00:00Z",
            "apps": [self._update()],
        }
        store = self._store(current)
        with patch("run_signing.trigger_revalidate") as mock_revalidate:
            ok = publish_registry(store, [self._update()], ["ehpanda"], "https://x/revalidate", "sec")
        assert ok is True
        store.upload_json.assert_not_called()
        mock_revalidate.assert_not_called()
        store.cleanup_stale.assert_called_once()

    def test_revalidate_failure_skips_cleanup(self) -> None:
        store = self._store(None)
        with patch("run_signing.trigger_revalidate", return_value=False):
            ok = publish_registry(store, [self._update()], ["ehpanda"], "https://x/revalidate", "sec")
        assert ok is False
        store.cleanup_stale.assert_not_called()

    def test_download_failure_aborts(self) -> None:
        store = self._store(None)
        store.download_json.side_effect = RuntimeError("boom")
        ok = publish_registry(store, [self._update()], ["ehpanda"], "https://x/revalidate", "sec")
        assert ok is False
        store.upload_json.assert_not_called()
        store.cleanup_stale.assert_not_called()


class TestBuildZsignArgv:
    """Tests for the zsign re-sign argv builder."""

    def _argv(self, tmp_path: Path, **kwargs: object) -> list[str]:
        return build_zsign_argv(
            "/usr/local/bin/zsign",
            tmp_path / "cert.p12",
            "s3cr3t",
            tmp_path / "profile.mobileprovision",
            tmp_path / "in.ipa",
            tmp_path / "out.ipa",
            **kwargs,  # type: ignore[arg-type]
        )

    def test_includes_core_flags(self, tmp_path: Path) -> None:
        argv = self._argv(tmp_path)
        assert argv[0] == "/usr/local/bin/zsign"
        # p12 + password, profile, output and input IPA are all wired up.
        assert argv[argv.index("-k") + 1] == str(tmp_path / "cert.p12")
        assert argv[argv.index("-p") + 1] == "s3cr3t"
        assert argv[argv.index("-m") + 1] == str(tmp_path / "profile.mobileprovision")
        assert argv[argv.index("-o") + 1] == str(tmp_path / "out.ipa")
        # The input IPA is the trailing positional argument.
        assert argv[-1] == str(tmp_path / "in.ipa")

    def test_forces_clean_sign(self, tmp_path: Path) -> None:
        """``-f`` avoids reusing a stale per-folder signing cache."""
        assert "-f" in self._argv(tmp_path)

    def test_default_and_custom_zip_level(self, tmp_path: Path) -> None:
        default = self._argv(tmp_path)
        assert default[default.index("-z") + 1] == "9"
        custom = self._argv(tmp_path, zip_level=0)
        assert custom[custom.index("-z") + 1] == "0"

    def test_bundle_id_rewrites_via_b_flag(self, tmp_path: Path) -> None:
        """``-b`` must carry the task's bundle id so the signed app matches
        the explicit App ID its provisioning profile was issued for."""
        argv = self._argv(tmp_path, bundle_id="io.zeroclover.app.example")
        assert argv[argv.index("-b") + 1] == "io.zeroclover.app.example"
        # -b must come before the -o/output & trailing input positional.
        assert argv.index("-b") < argv.index("-o")

    def test_bundle_id_omitted_by_default(self, tmp_path: Path) -> None:
        assert "-b" not in self._argv(tmp_path)

    def test_argv_is_shell_free(self, tmp_path: Path) -> None:
        """Every element is a plain string suitable for shell-less subprocess."""
        argv = self._argv(tmp_path)
        assert all(isinstance(part, str) for part in argv)


class TestBuildP12NormalizeCommands:
    """Tests for the Apple-P12 -> modern-AES-P12 openssl command builder."""

    def _cmds(self, tmp_path: Path, openssl_bin: str = "openssl"):
        return build_p12_normalize_commands(
            tmp_path / "apple.p12",
            tmp_path / "cert.pem",
            tmp_path / "cert.p12",
            "PW",
            openssl_bin,
        )

    def test_extract_uses_legacy_provider(self, tmp_path: Path) -> None:
        """First command must enable the legacy provider to read RC2-encrypted P12."""
        extract, _ = self._cmds(tmp_path)
        assert extract[:3] == ["openssl", "pkcs12", "-legacy"]
        assert "-nodes" in extract
        assert extract[extract.index("-in") + 1] == str(tmp_path / "apple.p12")
        assert extract[extract.index("-out") + 1] == str(tmp_path / "cert.pem")

    def test_repack_exports_modern_p12(self, tmp_path: Path) -> None:
        """Second command re-exports with OpenSSL 3 defaults (no -legacy)."""
        _, repack = self._cmds(tmp_path)
        assert repack[:3] == ["openssl", "pkcs12", "-export"]
        assert "-legacy" not in repack
        assert repack[repack.index("-in") + 1] == str(tmp_path / "cert.pem")
        assert repack[repack.index("-out") + 1] == str(tmp_path / "cert.p12")

    def test_password_passed_via_env_not_argv(self, tmp_path: Path) -> None:
        """The password is referenced via env:, never embedded as a literal."""
        extract, repack = self._cmds(tmp_path)
        assert extract[extract.index("-passin") + 1] == "env:PW"
        assert repack[repack.index("-passout") + 1] == "env:PW"

    def test_custom_openssl_bin(self, tmp_path: Path) -> None:
        extract, repack = self._cmds(tmp_path, openssl_bin="/opt/ossl/bin/openssl")
        assert extract[0] == "/opt/ossl/bin/openssl"
        assert repack[0] == "/opt/ossl/bin/openssl"


class TestFindZsign:
    """Tests for locating the zsign executable."""

    def test_prefers_zsign_bin_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZSIGN_BIN", "/custom/path/zsign")
        assert find_zsign() == "/custom/path/zsign"

    def test_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ZSIGN_BIN", raising=False)
        monkeypatch.setattr("run_signing.shutil.which", lambda _: "/opt/bin/zsign")
        assert find_zsign() == "/opt/bin/zsign"

    def test_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ZSIGN_BIN", raising=False)
        monkeypatch.setattr("run_signing.shutil.which", lambda _: None)
        with pytest.raises(FileNotFoundError, match="zsign not found"):
            find_zsign()
