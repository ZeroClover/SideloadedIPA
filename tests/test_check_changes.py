"""Tests for scripts/check_changes.py"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from check_changes import (
    calculate_checksum,
    compare_device_lists,
    get_tasks_to_rebuild,
    load_json_file,
    load_tasks,
    parse_repo_url,
)


class TestCalculateChecksum:
    """Tests for device list checksum calculation."""

    def test_checksum_format(self, sample_device_list: List[Dict]) -> None:
        """Checksum should be in sha256:hexdigest format."""
        checksum = calculate_checksum(sample_device_list)
        assert checksum.startswith("sha256:")
        # SHA256 hex digest is 64 characters
        assert len(checksum.split(":")[1]) == 64

    def test_checksum_deterministic(self, sample_device_list: List[Dict]) -> None:
        """Same data should produce same checksum."""
        checksum1 = calculate_checksum(sample_device_list)
        checksum2 = calculate_checksum(sample_device_list)
        assert checksum1 == checksum2

    def test_checksum_order_independent(self, sample_device_list: List[Dict]) -> None:
        """Checksum should be same regardless of device order."""
        reversed_list = list(reversed(sample_device_list))
        checksum1 = calculate_checksum(sample_device_list)
        checksum2 = calculate_checksum(reversed_list)
        assert checksum1 == checksum2

    def test_checksum_changes_on_device_add(self, sample_device_list: List[Dict]) -> None:
        """Checksum should change when device is added."""
        original_checksum = calculate_checksum(sample_device_list)

        modified_list = sample_device_list + [
            {
                "id": "DEVICE003",
                "name": "New iPhone",
                "platform": "IOS",
                "device_class": "IPHONE",
                "udid": "00000000-0000-0000-0000-000000000003",
                "status": "ENABLED",
            }
        ]
        new_checksum = calculate_checksum(modified_list)
        assert original_checksum != new_checksum

    def test_checksum_changes_on_device_remove(self, sample_device_list: List[Dict]) -> None:
        """Checksum should change when device is removed."""
        original_checksum = calculate_checksum(sample_device_list)
        modified_list = sample_device_list[:1]  # Keep only first device
        new_checksum = calculate_checksum(modified_list)
        assert original_checksum != new_checksum

    def test_checksum_changes_on_device_modify(self, sample_device_list: List[Dict]) -> None:
        """Checksum should change when device data is modified."""
        original_checksum = calculate_checksum(sample_device_list)

        modified_list = [dict(d) for d in sample_device_list]  # Deep copy
        modified_list[0]["name"] = "Renamed iPhone"
        new_checksum = calculate_checksum(modified_list)
        assert original_checksum != new_checksum

    def test_empty_device_list(self) -> None:
        """Empty device list should produce valid checksum."""
        checksum = calculate_checksum([])
        assert checksum.startswith("sha256:")
        assert len(checksum.split(":")[1]) == 64


class TestLoadJsonFile:
    """Tests for JSON file loading."""

    def test_load_valid_json(self, tmp_path: Path) -> None:
        """Should load valid JSON file."""
        json_path = tmp_path / "test.json"
        data = {"key": "value", "number": 42}
        json_path.write_text(json.dumps(data))

        result = load_json_file(json_path)
        assert result == data

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """Should return None for non-existent file."""
        json_path = tmp_path / "nonexistent.json"
        result = load_json_file(json_path)
        assert result is None

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Should return None for invalid JSON."""
        json_path = tmp_path / "invalid.json"
        json_path.write_text("not valid json {")

        result = load_json_file(json_path)
        assert result is None


class TestCompareDeviceLists:
    """Tests for device list comparison."""

    def test_no_cached_device_list(
        self, temp_work_dir: Path, sample_device_list_json: Dict
    ) -> None:
        """Should return devices_changed=True when no cache exists."""
        cached_path = temp_work_dir / "work" / "cache-old" / "device-list.json"
        current_path = temp_work_dir / "work" / "cache" / "device-list.json"

        # Only create current file
        current_path.write_text(json.dumps(sample_device_list_json))

        devices_changed, cached, current = compare_device_lists(cached_path, current_path)

        assert devices_changed is True
        assert cached is None
        assert current is not None

    def test_no_current_device_list(
        self, temp_work_dir: Path, sample_device_list_json: Dict
    ) -> None:
        """Should return devices_changed=True when current list is missing."""
        cached_path = temp_work_dir / "work" / "cache-old" / "device-list.json"
        current_path = temp_work_dir / "work" / "cache" / "device-list.json"

        # Only create cached file
        cached_path.write_text(json.dumps(sample_device_list_json))

        devices_changed, cached, current = compare_device_lists(cached_path, current_path)

        assert devices_changed is True
        assert cached is not None
        assert current is None

    def test_matching_device_lists(
        self, temp_work_dir: Path, sample_device_list_json: Dict
    ) -> None:
        """Should return devices_changed=False when lists match."""
        cached_path = temp_work_dir / "work" / "cache-old" / "device-list.json"
        current_path = temp_work_dir / "work" / "cache" / "device-list.json"

        # Create both files with same checksum
        cached_path.write_text(json.dumps(sample_device_list_json))
        current_path.write_text(json.dumps(sample_device_list_json))

        devices_changed, cached, current = compare_device_lists(cached_path, current_path)

        assert devices_changed is False
        assert cached is not None
        assert current is not None

    def test_different_device_lists(
        self, temp_work_dir: Path, sample_device_list_json: Dict
    ) -> None:
        """Should return devices_changed=True when checksums differ."""
        cached_path = temp_work_dir / "work" / "cache-old" / "device-list.json"
        current_path = temp_work_dir / "work" / "cache" / "device-list.json"

        # Create cached file
        cached_path.write_text(json.dumps(sample_device_list_json))

        # Create current file with different checksum
        current_data = dict(sample_device_list_json)
        current_data["checksum"] = "sha256:different123"
        current_path.write_text(json.dumps(current_data))

        devices_changed, cached, current = compare_device_lists(cached_path, current_path)

        assert devices_changed is True

    def test_current_without_checksum(
        self, temp_work_dir: Path, sample_device_list: List[Dict]
    ) -> None:
        """Should calculate checksum if current doesn't have one."""
        cached_path = temp_work_dir / "work" / "cache-old" / "device-list.json"
        current_path = temp_work_dir / "work" / "cache" / "device-list.json"

        # Create cached with checksum
        cached_data = {
            "devices": sample_device_list,
            "checksum": calculate_checksum(sample_device_list),
        }
        cached_path.write_text(json.dumps(cached_data))

        # Create current without checksum
        current_data = {"devices": sample_device_list}
        current_path.write_text(json.dumps(current_data))

        devices_changed, cached, current = compare_device_lists(cached_path, current_path)

        # Should match since device lists are identical
        assert devices_changed is False


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

    def test_url_with_path(self) -> None:
        """Should handle URL with additional path."""
        owner, repo = parse_repo_url("https://github.com/owner/repo/tree/main")
        assert owner == "owner"
        assert repo == "repo"


class TestLoadTasks:
    """Tests for TOML task loading."""

    def test_load_valid_tasks(self, sample_tasks_toml: Path) -> None:
        """Should load tasks from valid TOML file."""
        tasks = load_tasks(sample_tasks_toml)
        assert len(tasks) == 2
        assert tasks[0]["task_name"] == "TestApp"
        assert tasks[1]["task_name"] == "DirectURLApp"

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """Should exit with error for non-existent file."""
        with pytest.raises(SystemExit) as exc_info:
            load_tasks(tmp_path / "nonexistent.toml")
        assert exc_info.value.code == 2

    def test_load_empty_tasks(self, tmp_path: Path) -> None:
        """Should return empty list for TOML with no tasks."""
        toml_path = tmp_path / "empty.toml"
        toml_path.write_text("# Empty TOML\n")

        tasks = load_tasks(toml_path)
        assert tasks == []


class TestGetTasksToRebuild:
    """Tests for rebuild list generation."""

    def test_rebuild_all_returns_all_tasks(
        self, temp_work_dir: Path, sample_tasks_toml: Path
    ) -> None:
        """When rebuild_all is True, should return all task names."""
        tasks = load_tasks(sample_tasks_toml)
        release_cache_path = temp_work_dir / "work" / "cache" / "release-versions.json"

        rebuild_tasks = get_tasks_to_rebuild(
            tasks, release_cache_path, rebuild_all=True, github_token=None
        )

        assert rebuild_tasks == {"TestApp", "DirectURLApp"}

    def test_direct_url_always_rebuilds(
        self, temp_work_dir: Path, sample_tasks_toml: Path
    ) -> None:
        """Tasks with ipa_url should always be in rebuild list."""
        tasks = load_tasks(sample_tasks_toml)
        release_cache_path = temp_work_dir / "work" / "cache" / "release-versions.json"

        # Create empty cache
        release_cache_path.write_text(json.dumps({"tasks": {}}))

        # Mock GitHub API to avoid actual calls
        with patch("check_changes.check_github_release_version") as mock_check:
            mock_check.return_value = (False, "up_to_date")

            rebuild_tasks = get_tasks_to_rebuild(
                tasks, release_cache_path, rebuild_all=False, github_token="test_token"
            )

        # DirectURLApp should always be in rebuild list
        assert "DirectURLApp" in rebuild_tasks

    def test_new_task_always_rebuilds(
        self,
        temp_work_dir: Path,
        env_with_github_token: None,
    ) -> None:
        """New tasks not in cache should be rebuilt."""
        tasks = [
            {
                "task_name": "NewApp",
                "app_name": "New App",
                "bundle_id": "com.example.newapp",
                "repo_url": "https://github.com/example/newapp",
                "asset_server_path": "/var/www/",
            }
        ]
        release_cache_path = temp_work_dir / "work" / "cache" / "release-versions.json"

        # Create cache without this task
        release_cache_path.write_text(json.dumps({"tasks": {}}))

        # Mock GitHub API to return first_run
        with patch("check_changes.check_github_release_version") as mock_check:
            mock_check.return_value = (True, "first_run")

            rebuild_tasks = get_tasks_to_rebuild(
                tasks, release_cache_path, rebuild_all=False, github_token="test_token"
            )

        assert "NewApp" in rebuild_tasks

    def test_missing_source_config(self, temp_work_dir: Path) -> None:
        """Tasks without ipa_url or repo_url should be rebuilt with warning."""
        tasks = [
            {
                "task_name": "BrokenApp",
                "app_name": "Broken App",
                "bundle_id": "com.example.broken",
                "asset_server_path": "/var/www/",
                # Missing both ipa_url and repo_url
            }
        ]
        release_cache_path = temp_work_dir / "work" / "cache" / "release-versions.json"

        rebuild_tasks = get_tasks_to_rebuild(
            tasks, release_cache_path, rebuild_all=False, github_token=None
        )

        assert "BrokenApp" in rebuild_tasks


class TestForceRebuild:
    """Tests for force rebuild override."""

    def test_force_rebuild_env_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FORCE_REBUILD=true should trigger rebuild all."""
        monkeypatch.setenv("FORCE_REBUILD", "true")
        force_rebuild = os.getenv("FORCE_REBUILD", "false").lower() in ("true", "1", "yes")
        assert force_rebuild is True

    def test_force_rebuild_env_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FORCE_REBUILD=1 should trigger rebuild all."""
        monkeypatch.setenv("FORCE_REBUILD", "1")
        force_rebuild = os.getenv("FORCE_REBUILD", "false").lower() in ("true", "1", "yes")
        assert force_rebuild is True

    def test_force_rebuild_env_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FORCE_REBUILD=yes should trigger rebuild all."""
        monkeypatch.setenv("FORCE_REBUILD", "yes")
        force_rebuild = os.getenv("FORCE_REBUILD", "false").lower() in ("true", "1", "yes")
        assert force_rebuild is True

    def test_force_rebuild_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FORCE_REBUILD=false should not trigger rebuild all."""
        monkeypatch.setenv("FORCE_REBUILD", "false")
        force_rebuild = os.getenv("FORCE_REBUILD", "false").lower() in ("true", "1", "yes")
        assert force_rebuild is False

    def test_force_rebuild_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing FORCE_REBUILD should default to false."""
        monkeypatch.delenv("FORCE_REBUILD", raising=False)
        force_rebuild = os.getenv("FORCE_REBUILD", "false").lower() in ("true", "1", "yes")
        assert force_rebuild is False
