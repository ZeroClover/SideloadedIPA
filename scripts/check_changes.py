#!/usr/bin/env python3
"""
Change detection script for CI caching optimization.

This script compares cached state (device lists and release versions) with
current state to determine which tasks need to be rebuilt.

Outputs:
- REBUILD_ALL: 'true' if device list changed or force_rebuild flag set
- REBUILD_TASKS: JSON array of task names that need rebuilding
"""

import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.error import HTTPError, URLError

# Prefer Python 3.11+'s tomllib; fallback to tomli if needed
try:
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except Exception as e:
        print(f"[error] tomllib/tomli not available: {e}", file=sys.stderr)
        sys.exit(1)


def is_truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def calculate_checksum(data: List[Dict]) -> str:
    """
    Calculate SHA-256 checksum of normalized device data.

    Args:
        data: List of device dictionaries

    Returns:
        Checksum string in format "sha256:hexdigest"
    """
    # Normalize: sort by device ID to ensure deterministic ordering
    normalized = sorted(data, key=lambda d: d.get("id", ""))
    json_str = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_json_file(path: Path) -> Optional[Dict]:
    """Load JSON file, return None if doesn't exist or invalid."""
    if not path.exists():
        return None
    try:
        with path.open("r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] Failed to load {path}: {e}", file=sys.stderr)
        return None


def compare_device_lists(
    cached_path: Path, current_path: Path
) -> tuple[bool, Optional[Dict], Optional[Dict]]:
    """
    Compare cached and current device lists.

    Args:
        cached_path: Path to OLD cached device-list.json
        current_path: Path to CURRENT device-list.json

    Returns:
        Tuple of (devices_changed, cached_data, current_data)
    """
    cached = load_json_file(cached_path)
    current = load_json_file(current_path)

    # No cache = first run, rebuild all
    if cached is None:
        print("[info] No cached device list found - first run, will rebuild all")
        return (True, None, current)

    # No current device list = error condition, rebuild all to be safe
    if current is None:
        print("[error] No current device list found - will rebuild all", file=sys.stderr)
        return (True, cached, None)

    # Compare checksums
    cached_checksum = cached.get("checksum", "")
    current_checksum = current.get("checksum", "")

    if not current_checksum:
        # Current doesn't have checksum, calculate it
        devices = current.get("devices", [])
        current_checksum = calculate_checksum(devices)
        print(f"[info] Calculated current checksum: {current_checksum}")

    if cached_checksum != current_checksum:
        print("[info] Device list changed:")
        print(f"  Cached checksum:  {cached_checksum}")
        print(f"  Current checksum: {current_checksum}")

        # Calculate detailed device changes
        cached_devices = {d.get("id"): d for d in cached.get("devices", [])}
        current_devices = {d.get("id"): d for d in current.get("devices", [])}

        cached_ids = set(cached_devices.keys())
        current_ids = set(current_devices.keys())

        added_ids = current_ids - cached_ids
        removed_ids = cached_ids - current_ids

        if added_ids:
            print(f"  → {len(added_ids)} device(s) added:")
            for device_id in added_ids:
                device = current_devices[device_id]
                print(f"     + {device.get('name')} ({device.get('device_class')})")

        if removed_ids:
            print(f"  → {len(removed_ids)} device(s) removed:")
            for device_id in removed_ids:
                device = cached_devices[device_id]
                print(f"     - {device.get('name')} ({device.get('device_class')})")

        return (True, cached, current)

    print("[info] Device list unchanged")
    return (False, cached, current)


def load_tasks(toml_path: Path) -> List[Dict]:
    """Load tasks from TOML configuration."""
    if not toml_path.exists():
        print(f"[error] Config file not found: {toml_path}", file=sys.stderr)
        sys.exit(2)

    with toml_path.open("rb") as f:
        config = tomllib.load(f)

    return config.get("tasks", [])


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """
    Parse GitHub repository URL to extract owner and repo name.

    Args:
        repo_url: GitHub repository URL (e.g., "https://github.com/owner/repo")

    Returns:
        Tuple of (owner, repo)
    """
    match = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", repo_url)
    if not match:
        raise ValueError(f"Invalid GitHub repository URL: {repo_url}")
    return match.group(1), match.group(2)


def check_github_release_version(
    task: dict,
    task_name: str,
    cached_versions: dict,
    github_token: Optional[str],
) -> tuple[bool, Optional[str]]:
    """
    Check if GitHub release version has changed for a task.

    Args:
        task: Task configuration
        task_name: Task name
        cached_versions: Cached version data
        github_token: GitHub token for API access

    Returns:
        Tuple of (has_changed, reason)
    """
    repo_url = task.get("repo_url")
    if not repo_url:
        return False, None

    try:
        owner, repo = parse_repo_url(repo_url)
    except ValueError as e:
        print(f"[warn] Task '{task_name}': {e}", file=sys.stderr)
        return True, "invalid_repo_url"

    use_prerelease = task.get("use_prerelease", False)

    # Fetch current release via GitHub API
    if not use_prerelease:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    else:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req) as response:
            rate_remaining = response.headers.get("X-RateLimit-Remaining")
            rate_reset = response.headers.get("X-RateLimit-Reset")

            if rate_remaining:
                remaining = int(rate_remaining)
                if is_truthy_env("DEBUG"):
                    print(f"[debug] GitHub API rate limit: {remaining} requests remaining")
                if remaining < 100:
                    print(f"[warn] GitHub API rate limit low: {remaining} requests remaining")
                    if rate_reset:
                        reset_time = datetime.datetime.fromtimestamp(int(rate_reset))
                        print(f"[warn] Rate limit resets at: {reset_time}")

            data = response.read().decode("utf-8")
            if use_prerelease:
                releases = json.loads(data)
                release = None
                for release_item in releases:
                    if release_item.get("prerelease"):
                        release = release_item
                        break
                if not release and releases:
                    release = releases[0]
                if not release:
                    print(f"[warn] Task '{task_name}': no releases found")
                    return True, "no_releases"
            else:
                release = json.loads(data)

            current_version = release.get("tag_name")
            current_published_at = release.get("published_at")

            cached_task = cached_versions.get(task_name, {})
            cached_version = cached_task.get("version")
            cached_published_at = cached_task.get("published_at")

            if not cached_version:
                print(f"[info] Task '{task_name}': first run (no cache)")
                return True, "first_run"

            if cached_version != current_version:
                print(
                    f"[info] Task '{task_name}': version changed {cached_version} → {current_version}"
                )
                return True, "version_changed"

            if cached_published_at != current_published_at:
                print(f"[info] Task '{task_name}': release republished")
                return True, "republished"

            print(f"[info] Task '{task_name}': up to date ({current_version})")
            return False, "up_to_date"

    except HTTPError as e:
        if e.code == 403:
            rate_reset = None
            if hasattr(e, "headers"):
                rate_reset = e.headers.get("X-RateLimit-Reset")
            print("[error] GitHub API rate limit exceeded", file=sys.stderr)
            if rate_reset:
                try:
                    reset_time = datetime.datetime.fromtimestamp(int(rate_reset))
                    print(f"[error] Rate limit resets at: {reset_time}", file=sys.stderr)
                except (ValueError, OSError):
                    pass
            sys.exit(1)

        print(f"[error] Task '{task_name}': GitHub API error {e.code}", file=sys.stderr)
        return True, "api_error"
    except URLError as e:
        print(
            f"[error] Task '{task_name}': GitHub API request failed: {e.reason}",
            file=sys.stderr,
        )
        return True, "api_error"
    except Exception as e:
        print(f"[error] Task '{task_name}': Failed to check release: {e}", file=sys.stderr)
        return True, "error"


def get_tasks_to_rebuild(
    tasks: List[Dict],
    release_cache_path: Path,
    rebuild_all: bool,
    github_token: Optional[str],
) -> Set[str]:
    """
    Determine which tasks need rebuilding based on version cache.

    Args:
        tasks: List of task dictionaries
        release_cache_path: Path to cached release-versions.json
        rebuild_all: If True, rebuild all tasks
        github_token: GitHub token for API access

    Returns:
        Set of task names that need rebuilding
    """
    rebuild_tasks: Set[str] = set()

    all_task_names = {task.get("task_name") for task in tasks if task.get("task_name")}
    if rebuild_all:
        return all_task_names

    release_cache = load_json_file(release_cache_path)
    cached_versions = release_cache.get("tasks", {}) if release_cache else {}

    for task in tasks:
        task_name = task.get("task_name")
        if not task_name:
            continue

        # Tasks with ipa_url always rebuild (no version tracking)
        if task.get("ipa_url"):
            rebuild_tasks.add(task_name)
            print(f"[info] Task '{task_name}': direct URL (always rebuild)")
            continue

        # Tasks with repo_url: check if version changed
        if task.get("repo_url"):
            needs_rebuild, _reason = check_github_release_version(
                task, task_name, cached_versions, github_token
            )
            if needs_rebuild:
                rebuild_tasks.add(task_name)
            continue

        # Task has neither ipa_url nor repo_url - invalid but rebuild anyway
        rebuild_tasks.add(task_name)
        print(f"[warn] Task '{task_name}': no IPA source defined", file=sys.stderr)

    return rebuild_tasks


def main() -> int:
    """Main entry point."""
    # Paths
    cache_dir = Path("work/cache")
    cache_old_dir = Path("work/cache-old")
    cached_device_list = cache_old_dir / "device-list.json"  # OLD cached version
    current_device_list = cache_dir / "device-list.json"  # NEW current version
    release_cache = cache_dir / "release-versions.json"
    toml_path = Path(os.getenv("CONFIG_TOML", "configs/tasks.toml"))

    # Check for force rebuild flag
    force_rebuild = os.getenv("FORCE_REBUILD", "false").lower() in ("true", "1", "yes")

    if force_rebuild:
        print("[info] Force rebuild enabled - will rebuild all tasks")

    tasks = load_tasks(toml_path)
    has_repo_tasks = any(task.get("repo_url") for task in tasks)

    # Compare device lists
    devices_changed, _, _ = compare_device_lists(cached_device_list, current_device_list)

    rebuild_all = force_rebuild or devices_changed

    # Get GitHub token for API access
    github_token = os.getenv("GITHUB_TOKEN")
    if has_repo_tasks:
        if not github_token:
            print(
                "[error] GITHUB_TOKEN environment variable is required for GitHub API access.\n"
                "Ensure the workflow has 'contents: read' permission.",
                file=sys.stderr,
            )
            return 2
        print("[info] GitHub API: Using authenticated access")

    # Determine tasks to rebuild
    rebuild_tasks = get_tasks_to_rebuild(tasks, release_cache, rebuild_all, github_token)

    # Output results for GitHub Actions
    github_output = os.getenv("GITHUB_OUTPUT")

    if github_output:
        with open(github_output, "a") as f:
            f.write(f"rebuild_all={'true' if rebuild_all else 'false'}\n")
            f.write(f"rebuild_tasks={json.dumps(sorted(rebuild_tasks))}\n")

    # Also output to stdout for debugging
    print("\n[summary] Change detection results:")
    print(f"  REBUILD_ALL: {'true' if rebuild_all else 'false'}")
    print(f"  REBUILD_TASKS: {json.dumps(sorted(rebuild_tasks))}")

    if force_rebuild:
        print("\n[action] Force rebuild requested - will rebuild all tasks")
    elif devices_changed:
        print("\n[action] Device list changed - will regenerate profiles and rebuild all tasks")
    elif rebuild_tasks:
        print(
            f"\n[action] Will rebuild {len(rebuild_tasks)} task(s): {', '.join(sorted(rebuild_tasks))}"
        )
    else:
        print("\n[action] No tasks need rebuilding")

    return 0


if __name__ == "__main__":
    sys.exit(main())
