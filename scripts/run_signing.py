#!/usr/bin/env python3
import base64
import datetime
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError

# Prefer Python 3.11+'s tomllib; fallback to tomli if needed
try:
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except Exception as e:
        print("tomllib/tomli not available. Install tomli or use Python 3.11+.", file=sys.stderr)
        raise e


def is_truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def run(cmd: str, cwd: Path | None = None) -> None:
    print(f"[run] {cmd}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, shell=True, check=True)


def slugify_filename(name: str) -> str:
    # Replace non-alphanumeric with underscores, collapse repeats
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("._-") or "app"


def read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def decode_b64_to_file(b64: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(base64.b64decode(b64))


def find_bundle_exec() -> str:
    """Return the bundle exec command prefix."""
    from shutil import which

    bundle_exe = which("bundle")
    if not bundle_exe:
        raise FileNotFoundError(
            "bundle not found in PATH. Ensure bundler is installed on the runner."
        )
    return f"{bundle_exe} exec"


def discover_codesign_identity(keychain_path: Optional[str]) -> Optional[str]:
    """Try to discover an Apple codesigning identity from a keychain or default search list."""
    cmd = ["security", "find-identity", "-v", "-p", "codesigning"]
    if keychain_path:
        cmd.append(keychain_path)
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except Exception:
        return None
    # Parse lines like:  1) XXXXXXX "Apple Distribution: Name (TEAMID)"
    for line in out.splitlines():
        if "Apple Development" in line or "Apple Distribution" in line or "iPhone" in line:
            # extract quoted name
            m = re.search(r'"([^"]+)"', line)
            if m:
                return m.group(1)
    return None


def build_remote_dest(base_path: str, filename: str) -> str:
    # If base_path ends with '/', treat as directory and append filename
    if base_path.endswith("/"):
        return f"{base_path}{filename}"
    # Otherwise, treat as exact destination file path
    return base_path


def ensure_remote_dir(user: str, host: str, password: str, dest_path: str) -> None:
    remote_dir = os.path.dirname(dest_path) or "."
    ssh_cmd = (
        f"sshpass -p {shlex.quote(password)} ssh -o StrictHostKeyChecking=no "
        f"{shlex.quote(user)}@{shlex.quote(host)} "
        f"{shlex.quote(f'mkdir -p {shlex.quote(remote_dir)}')}"
    )
    run(ssh_cmd)


# GitHub API integration
class GitHubAPIClient:
    """Client for GitHub REST API with authentication and rate limit handling."""

    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError(
                "GITHUB_TOKEN environment variable is required for GitHub API access.\n"
                "Ensure the workflow has 'contents: read' permission."
            )
        print("[info] GitHub API: Using authenticated access")

    def _make_request(self, url: str) -> dict:
        """Make authenticated GitHub API request with rate limit handling."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        req = urllib.request.Request(url, headers=headers)
        rate_reset = None  # Initialize to avoid reference error in except block

        try:
            with urllib.request.urlopen(req) as response:
                # Check rate limit headers
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
                return json.loads(data)

        except HTTPError as e:
            if e.code == 403:
                # Try to get rate limit headers from error response
                if hasattr(e, "headers"):
                    rate_reset = e.headers.get("X-RateLimit-Reset")

                print("[error] GitHub API rate limit exceeded", file=sys.stderr)
                if rate_reset:
                    try:
                        reset_time = datetime.datetime.fromtimestamp(int(rate_reset))
                        print(f"[error] Rate limit resets at: {reset_time}", file=sys.stderr)
                    except (ValueError, OSError):
                        pass
                raise
            elif e.code == 404:
                print(f"[error] GitHub API: Resource not found: {url}", file=sys.stderr)
                raise
            else:
                print(f"[error] GitHub API request failed: {e.code} {e.reason}", file=sys.stderr)
                raise
        except URLError as e:
            print(f"[error] GitHub API request failed: {e.reason}", file=sys.stderr)
            raise

    def fetch_latest_release(
        self, owner: str, repo: str, use_prerelease: bool = False
    ) -> Optional[dict]:
        """
        Fetch latest release from GitHub repository.

        Args:
            owner: Repository owner
            repo: Repository name
            use_prerelease: If True, fetch latest prerelease; otherwise latest stable

        Returns:
            Release data dict or None if no release found
        """
        if not use_prerelease:
            # Use /releases/latest endpoint for stable releases
            url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            print(f"[info] Fetching latest stable release from {owner}/{repo}")

            try:
                return self._make_request(url)
            except HTTPError as e:
                if e.code == 404:
                    print(f"[warn] No stable release found for {owner}/{repo}")
                    return None
                raise
        else:
            # Fetch all releases and find latest prerelease
            url = f"https://api.github.com/repos/{owner}/{repo}/releases"
            print(f"[info] Fetching latest prerelease from {owner}/{repo}")

            try:
                releases = self._make_request(url)
                if not releases:
                    print(f"[warn] No releases found for {owner}/{repo}")
                    return None

                # Find first prerelease
                for release in releases:
                    if release.get("prerelease"):
                        print(f"[info] Found prerelease: {release.get('tag_name')}")
                        return release

                # Fallback to latest stable if no prerelease found
                print("[info] No prerelease found, falling back to latest stable")
                if releases:
                    return releases[0]

                return None
            except HTTPError as e:
                if e.code == 404:
                    print(f"[warn] No releases found for {owner}/{repo}")
                    return None
                raise

    def find_matching_asset(self, release: dict, glob_pattern: str) -> Optional[dict]:
        """
        Find release asset matching glob pattern.

        Args:
            release: Release data from GitHub API
            glob_pattern: fnmatch-style pattern (e.g., "*.ipa")

        Returns:
            Asset dict or None if no match
        """
        assets = release.get("assets", [])
        if not assets:
            print("[warn] Release has no assets")
            return None

        matched_assets = []
        for asset in assets:
            name = asset.get("name", "")
            if fnmatch.fnmatch(name, glob_pattern):
                matched_assets.append(asset)

        if not matched_assets:
            print(f"[error] No assets match pattern '{glob_pattern}'")
            print(f"[error] Available assets: {[a.get('name') for a in assets]}")
            return None

        if len(matched_assets) > 1:
            names = [a.get("name") for a in matched_assets]
            print(f"[warn] Multiple assets match pattern '{glob_pattern}': {names}")
            print(f"[warn] Using first match: {matched_assets[0].get('name')}")

        return matched_assets[0]


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """
    Parse GitHub repository URL to extract owner and repo name.

    Args:
        repo_url: GitHub repository URL (e.g., "https://github.com/owner/repo")

    Returns:
        Tuple of (owner, repo)
    """
    # Support both HTTPS and git@ URLs
    patterns = [
        r"github\.com[:/]([^/]+)/([^/\.]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, repo_url)
        if match:
            return match.group(1), match.group(2)

    raise ValueError(f"Invalid GitHub repository URL: {repo_url}")


def validate_task(task: dict) -> tuple[bool, Optional[str]]:
    """
    Validate task configuration.

    Args:
        task: Task dict from TOML

    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = ["task_name", "app_name", "bundle_id", "asset_server_path"]
    for field in required_fields:
        if not task.get(field):
            return False, f"Missing required field: {field}"

    # Check mutually exclusive ipa_url and repo_url
    has_ipa_url = bool(task.get("ipa_url"))
    has_repo_url = bool(task.get("repo_url"))

    if not has_ipa_url and not has_repo_url:
        return False, "Must specify either 'ipa_url' or 'repo_url'"

    if has_ipa_url and has_repo_url:
        return False, "Cannot specify both 'ipa_url' and 'repo_url' (mutually exclusive)"

    # Validate ipa_url is HTTP/HTTPS if present
    if has_ipa_url:
        ipa_url = task["ipa_url"]
        if not ipa_url.startswith(("http://", "https://")):
            return False, f"ipa_url must be HTTP or HTTPS URL, got: {ipa_url}"

    # Validate repo_url format if present
    if has_repo_url:
        try:
            parse_repo_url(task["repo_url"])
        except ValueError as e:
            return False, str(e)

    return True, None


def load_release_cache(cache_path: Path) -> dict:
    """Load release version cache from JSON file."""
    if not cache_path.exists():
        return {"tasks": {}, "last_updated": None}

    try:
        with cache_path.open("r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] Failed to load release cache: {e}", file=sys.stderr)
        return {"tasks": {}, "last_updated": None}


def save_release_cache(cache_path: Path, cache_data: dict) -> None:
    """Save release version cache to JSON file."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_data["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        with cache_path.open("w") as f:
            json.dump(cache_data, f, indent=2)
        print(f"[info] Release cache saved to: {cache_path}")
    except Exception as e:
        print(f"[warn] Failed to save release cache: {e}", file=sys.stderr)


def should_rebuild_task(
    task: dict,
    task_name: str,
    cache_data: dict,
    github_client: Optional[GitHubAPIClient],
    force_rebuild: bool = False,
) -> tuple[bool, Optional[str], Optional[dict]]:
    """
    Check if task needs rebuilding based on version cache.

    Args:
        task: Task configuration
        task_name: Task name
        cache_data: Release cache data
        github_client: GitHub API client (if available)
        force_rebuild: If True, skip version comparison and rebuild unconditionally

    Returns:
        Tuple of (should_rebuild, download_url, version_info_to_cache)
        version_info_to_cache is dict to save to cache AFTER successful build, or None
    """
    # Always rebuild ipa_url tasks (no version tracking)
    if task.get("ipa_url"):
        return True, task["ipa_url"], None

    # GitHub release tasks
    if task.get("repo_url") and github_client:
        try:
            owner, repo = parse_repo_url(task["repo_url"])
            use_prerelease = task.get("use_prerelease", False)
            release_glob = task.get("release_glob", "*.ipa")

            # Fetch current release
            release = github_client.fetch_latest_release(owner, repo, use_prerelease)
            if not release:
                print(f"[error] No release found for {owner}/{repo}")
                return False, None, None

            # Find matching asset
            asset = github_client.find_matching_asset(release, release_glob)
            if not asset:
                return False, None, None

            current_version = release.get("tag_name")
            current_published_at = release.get("published_at")
            download_url = asset.get("browser_download_url")

            # Prepare version info to cache AFTER successful build
            version_info = {
                "version": current_version,
                "published_at": current_published_at,
                "download_url": download_url,
                "asset_id": asset.get("id"),
            }

            if force_rebuild:
                return True, download_url, version_info

            # Check cache to determine if rebuild needed
            cached_task = cache_data["tasks"].get(task_name, {})
            cached_version = cached_task.get("version")
            cached_published_at = cached_task.get("published_at")

            if not cached_version:
                print(f"[info] Task '{task_name}': first run (no cache)")
                return True, download_url, version_info

            if cached_version != current_version:
                print(f"[info] Task '{task_name}': version changed")
                print(f"  Cached: {cached_version} → Current: {current_version}")
                return True, download_url, version_info

            if cached_published_at != current_published_at:
                print(f"[info] Task '{task_name}': release republished")
                print(f"  Cached: {cached_published_at} → Current: {current_published_at}")
                return True, download_url, version_info

            print(f"[info] Task '{task_name}': up to date ({current_version})")
            return False, download_url, None

        except Exception as e:
            print(f"[error] Failed to check release for task '{task_name}': {e}", file=sys.stderr)
            return False, None, None

    return True, None, None


def main() -> int:
    config_path = Path(os.getenv("CONFIG_TOML", "configs/tasks.toml")).resolve()
    if not config_path.exists():
        print(f"[error] Config file not found: {config_path}", file=sys.stderr)
        return 2

    print(f"[info] Using config: {config_path}")
    cfg = read_toml(config_path)
    tasks = cfg.get("tasks", [])
    if not tasks:
        print("[warn] No tasks defined in TOML")
        return 0

    # Get rebuild directives from check_changes.py output
    rebuild_all = os.getenv("REBUILD_ALL", "false").lower() in ("true", "1", "yes")
    rebuild_tasks_env = os.getenv("REBUILD_TASKS")
    rebuild_tasks_set = set()
    has_rebuild_tasks = rebuild_tasks_env is not None
    if has_rebuild_tasks:
        try:
            rebuild_tasks_set = set(json.loads(rebuild_tasks_env))
        except json.JSONDecodeError as e:
            print(f"[error] Invalid REBUILD_TASKS JSON: {e}", file=sys.stderr)
            return 2

    if rebuild_all:
        print(
            "[info] REBUILD_ALL=true - will rebuild all tasks (device list changed or force rebuild)"
        )
    elif has_rebuild_tasks:
        if rebuild_tasks_set:
            print(f"[info] Using REBUILD_TASKS list: {sorted(rebuild_tasks_set)}")
        else:
            print("[info] REBUILD_TASKS is empty - no tasks scheduled for rebuild")
    else:
        print(
            "[info] REBUILD_TASKS not provided - will check each task individually for version changes"
        )

    # Validate tasks
    for task in tasks:
        valid, error = validate_task(task)
        if not valid:
            print(f"[error] Invalid task configuration: {error}", file=sys.stderr)
            print(f"[error] Task: {task}", file=sys.stderr)
            return 2

    # Validate required top-level envs
    required_envs = [
        "APPLE_DEV_CERT_PASSWORD",
        "ASSETS_SERVER_IP",
        "ASSETS_SERVER_USER",
        "ASSETS_SERVER_CREDENTIALS",
    ]
    for key in required_envs:
        if not os.getenv(key):
            print(f"[error] Missing required environment variable: {key}", file=sys.stderr)
            return 3

    assets_ip = os.environ["ASSETS_SERVER_IP"]
    assets_user = os.environ["ASSETS_SERVER_USER"]
    assets_pass = os.environ["ASSETS_SERVER_CREDENTIALS"]

    bundle_exec_cmd = find_bundle_exec()
    print(f"[info] Using bundle exec: {bundle_exec_cmd}")

    keychain_path = os.getenv("KEYCHAIN_PATH")  # optional; action may set default keychain
    codesign_identity = os.getenv("CODESIGN_IDENTITY") or discover_codesign_identity(keychain_path)
    if not codesign_identity:
        print(
            "[error] Code signing identity not found. Ensure certificates were imported and CODESIGN_IDENTITY is set.",
            file=sys.stderr,
        )
        return 4

    work_root = Path("work").resolve()
    work_root.mkdir(exist_ok=True)
    cache_dir = work_root / "cache"
    cache_dir.mkdir(exist_ok=True)

    # Initialize GitHub API client if needed
    github_client = None
    needs_github_api = any(task.get("repo_url") for task in tasks)
    if needs_github_api:
        try:
            github_client = GitHubAPIClient()
        except ValueError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 6

    # Load release cache
    release_cache_path = cache_dir / "release-versions.json"
    release_cache = load_release_cache(release_cache_path)

    any_fail = False
    processed_count = 0
    skipped_count = 0

    # Track rebuild reasons for detailed summary
    rebuild_reasons = {
        "device_changes": 0,
        "version_changes": 0,
        "first_run": 0,
        "direct_url": 0,
        "error": 0,
    }

    for i, task in enumerate(tasks, start=1):
        print("=" * 80)
        task_name = task.get("task_name")
        app_name = task.get("app_name")
        asset_server_path = task.get("asset_server_path")

        print(f"[task {i}] Starting: {task_name}")

        # Determine if task needs rebuilding
        version_info = None  # Will be set if task has cacheable version info
        rebuild_reason = None

        if rebuild_all:
            should_rebuild = True
            rebuild_reason = "device_changes"
            print(
                f"[task {i}] Rebuild required: REBUILD_ALL flag set (device changes or force rebuild)"
            )
            _, ipa_url, version_info = should_rebuild_task(
                task, task_name, release_cache, github_client, force_rebuild=True
            )
        elif has_rebuild_tasks:
            if task_name not in rebuild_tasks_set:
                print(f"[task {i}] Skipping: {task_name} (not in REBUILD_TASKS)")
                skipped_count += 1
                continue
            should_rebuild = True
            print(f"[task {i}] Rebuild required: task listed in REBUILD_TASKS")
            _, ipa_url, version_info = should_rebuild_task(
                task, task_name, release_cache, github_client, force_rebuild=True
            )
            if task.get("ipa_url"):
                rebuild_reason = "direct_url"
            elif version_info and not release_cache.get("tasks", {}).get(task_name):
                rebuild_reason = "first_run"
            elif version_info:
                rebuild_reason = "version_changes"
            else:
                rebuild_reason = "error"
        else:
            # Check each task individually for version changes
            should_rebuild, ipa_url, version_info = should_rebuild_task(
                task, task_name, release_cache, github_client
            )

            # Determine rebuild reason from should_rebuild_task result
            if should_rebuild:
                if task.get("ipa_url"):
                    rebuild_reason = "direct_url"
                elif version_info and not release_cache.get("tasks", {}).get(task_name):
                    rebuild_reason = "first_run"
                elif version_info:
                    rebuild_reason = "version_changes"
                else:
                    rebuild_reason = "error"

            if not should_rebuild:
                print(f"[task {i}] Skipping: {task_name} (already up to date)")
                skipped_count += 1
                continue

        if not ipa_url:
            print(f"[task {i}] Skipping: {task_name} (no download URL)", file=sys.stderr)
            any_fail = True
            rebuild_reasons["error"] += 1
            continue

        # Track rebuild reason
        if rebuild_reason:
            rebuild_reasons[rebuild_reason] += 1

        processed_count += 1

        safe_name = slugify_filename(app_name)
        tdir = work_root / safe_name
        tdir.mkdir(parents=True, exist_ok=True)

        # Use provisioning profile synced by sync_profiles.rb
        synced_profile = Path(f"work/profiles/{task_name}.mobileprovision")
        mobileprov_path = tdir / "profile.mobileprovision"

        if not synced_profile.exists():
            print(
                f"[task {i}] Provisioning profile not found: {synced_profile}\n"
                f"  Ensure sync_profiles.rb ran successfully and bundle_id is correct.",
                file=sys.stderr,
            )
            any_fail = True
            continue

        print(f"[task {i}] Using synced profile: {synced_profile}")
        shutil.copy2(synced_profile, mobileprov_path)

        # Download IPA
        ori_ipa = tdir / f"{safe_name}_ori.ipa"
        print(f"[task {i}] Downloading IPA from: {ipa_url}")
        curl_cmd = f"curl -fL --retry 3 --retry-all-errors -o {shlex.quote(str(ori_ipa))} {shlex.quote(ipa_url)}"
        try:
            run(curl_cmd)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] Failed to download IPA: {e}", file=sys.stderr)
            any_fail = True
            continue

        if not ori_ipa.exists() or ori_ipa.stat().st_size == 0:
            print(f"[task {i}] Downloaded IPA missing or empty: {ori_ipa}", file=sys.stderr)
            any_fail = True
            continue

        # Resign with fastlane
        signed_ipa = tdir / f"{safe_name}.ipa"
        resign_params = [
            shlex.quote(str(ori_ipa)),
            f"ipa:{shlex.quote(str(ori_ipa))}",
            f"signing_identity:{shlex.quote(codesign_identity)}",
            f"provisioning_profile:{shlex.quote(str(mobileprov_path))}",
        ]
        if keychain_path:
            resign_params.append(f"keychain_path:{shlex.quote(keychain_path)}")
        fl_cmd = f"{bundle_exec_cmd} fastlane run resign " + " ".join(resign_params)
        try:
            run(fl_cmd)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] fastlane resign failed: {e}", file=sys.stderr)
            any_fail = True
            continue

        # Determine resulting IPA
        result_ipa: Path = ori_ipa
        try:
            if result_ipa.exists():
                if result_ipa.resolve() != signed_ipa.resolve():
                    shutil.copy2(result_ipa, signed_ipa)
            else:
                latest_ipas = sorted(
                    tdir.glob("*.ipa"), key=lambda p: p.stat().st_mtime, reverse=True
                )
                if latest_ipas:
                    result_ipa = latest_ipas[0]
                    if result_ipa.resolve() != signed_ipa.resolve():
                        shutil.copy2(result_ipa, signed_ipa)
        except Exception as e:
            print(f"[task {i}] Failed to finalize IPA artifact: {e}", file=sys.stderr)
            any_fail = True
            continue

        if not signed_ipa.exists() or signed_ipa.stat().st_size == 0:
            print(f"[task {i}] Signed IPA missing or empty: {signed_ipa}", file=sys.stderr)
            any_fail = True
            continue

        # Upload via scp
        remote_dest = build_remote_dest(asset_server_path, f"{safe_name}.ipa")
        try:
            ensure_remote_dir(assets_user, assets_ip, assets_pass, remote_dest)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] Failed to ensure remote dir: {e}", file=sys.stderr)
            any_fail = True
            continue

        scp_cmd = (
            f"sshpass -p {shlex.quote(assets_pass)} "
            f"scp -o StrictHostKeyChecking=no "
            f"{shlex.quote(str(signed_ipa))} "
            f"{shlex.quote(assets_user)}@{shlex.quote(assets_ip)}:{shlex.quote(remote_dest)}"
        )
        try:
            run(scp_cmd)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] scp upload failed: {e}", file=sys.stderr)
            any_fail = True
            continue

        print(f"[task {i}] Completed: {app_name} -> {remote_dest}")

        # Update release cache ONLY after successful signing and upload
        if version_info:
            release_cache["tasks"][task_name] = version_info
            print(f"[task {i}] Updated version cache for: {task_name}")

    # Save release cache
    if github_client:
        save_release_cache(release_cache_path, release_cache)

    print("=" * 80)
    print(
        f"[summary] Tasks: {len(tasks)} total, {processed_count} processed, {skipped_count} skipped"
    )

    # Display detailed rebuild reasons
    if processed_count > 0:
        print("\n[summary] Rebuild reasons:")
        if rebuild_reasons["device_changes"] > 0:
            print(f"  • Device changes: {rebuild_reasons['device_changes']} task(s)")
        if rebuild_reasons["version_changes"] > 0:
            print(f"  • Version changes: {rebuild_reasons['version_changes']} task(s)")
        if rebuild_reasons["first_run"] > 0:
            print(f"  • First run (no cache): {rebuild_reasons['first_run']} task(s)")
        if rebuild_reasons["direct_url"] > 0:
            print(f"  • Direct URL (always rebuild): {rebuild_reasons['direct_url']} task(s)")
        if rebuild_reasons["error"] > 0:
            print(f"  • Errors: {rebuild_reasons['error']} task(s)")

    if any_fail:
        print("\n[summary] Some tasks failed. See logs above.", file=sys.stderr)
        return 5
    print("\n[summary] All tasks completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
