#!/usr/bin/env python3
import base64
import datetime
import fnmatch
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError

import apps_registry
import app_icon
import r2_store

# Vercel on-demand revalidation hook (shared-secret protected). Overridable via
# the VERCEL_REVALIDATE_URL env var (e.g. for preview deployments).
DEFAULT_REVALIDATE_URL = "https://itms.zeroclover.io/api/revalidate"

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


def find_zsign() -> str:
    """Return the path to the zsign executable built/installed on the runner."""
    from shutil import which

    zsign = os.getenv("ZSIGN_BIN") or which("zsign")
    if not zsign:
        raise FileNotFoundError(
            "zsign not found in PATH. Set ZSIGN_BIN or ensure the build step ran."
        )
    return zsign


def build_zsign_argv(
    zsign_bin: str,
    p12_path: Path,
    password: str,
    profile_path: Path,
    input_ipa: Path,
    output_ipa: Path,
    bundle_id: Optional[str] = None,
    zip_level: int = 9,
) -> list[str]:
    """Build the argv for a zsign re-sign invocation.

    Signs ``input_ipa`` with a p12 + password (``-k``/``-p``) and provisioning
    profile (``-m``), writing a freshly compressed IPA to ``output_ipa``
    (``-o``). ``-b`` rewrites CFBundleIdentifier to ``bundle_id`` — required
    for development-signed installs, where the app's bundle id must match the
    explicit App ID the provisioning profile was issued for. ``-f`` forces a
    clean sign with no stale per-folder cache. The argv is handed to
    ``subprocess`` directly (never a shell string), so the password is not
    subject to shell quoting and is never echoed to the CI log.
    """
    argv = [
        zsign_bin,
        "-f",
        "-z",
        str(zip_level),
        "-k",
        str(p12_path),
        "-p",
        password,
        "-m",
        str(profile_path),
    ]
    if bundle_id:
        argv += ["-b", bundle_id]
    argv += [
        "-o",
        str(output_ipa),
        str(input_ipa),
    ]
    return argv


def build_p12_normalize_commands(
    src_p12: Path,
    pem_path: Path,
    dst_p12: Path,
    password_env_var: str,
    openssl_bin: str = "openssl",
) -> tuple[list[str], list[str]]:
    """Build the two ``openssl`` commands that re-wrap an Apple P12 for zsign.

    Apple exports the signing certificate as a PKCS#12 encrypted with the legacy
    ``RC2-40-CBC`` cipher. OpenSSL 3's *default* provider — statically linked
    into the zsign ``musl`` binary — cannot read RC2, so zsign aborts with
    "RC2-40-CBC ... unsupported". The first command decrypts the bundle via the
    system OpenSSL's legacy provider into a plaintext PEM; the second re-exports
    it with OpenSSL 3 defaults (AES), which the default provider accepts.

    The password is passed via ``env:`` so it never appears in argv / the CI log.
    """
    extract = [
        openssl_bin,
        "pkcs12",
        "-legacy",
        "-in",
        str(src_p12),
        "-nodes",
        "-passin",
        f"env:{password_env_var}",
        "-out",
        str(pem_path),
    ]
    repack = [
        openssl_bin,
        "pkcs12",
        "-export",
        "-in",
        str(pem_path),
        "-out",
        str(dst_p12),
        "-passout",
        f"env:{password_env_var}",
    ]
    return extract, repack


def prepare_signing_p12(p12_b64: str, work_root: Path, password: str) -> Path:
    """Decode the base64 P12 and normalise its encryption so zsign can read it.

    Returns the path to a modern (AES-encrypted) P12. See
    :func:`build_p12_normalize_commands` for why the re-wrap is necessary.
    """
    raw_p12 = work_root / "cert_apple.p12"
    decode_b64_to_file(p12_b64, raw_p12)

    pem_path = work_root / "cert.pem"
    dst_p12 = work_root / "cert.p12"
    pw_var = "ZSIGN_P12_PW"
    openssl_bin = os.getenv("OPENSSL_BIN") or "openssl"
    extract, repack = build_p12_normalize_commands(raw_p12, pem_path, dst_p12, pw_var, openssl_bin)

    env = {**os.environ, pw_var: password}
    for argv in (extract, repack):
        subprocess.run(argv, env=env, check=True, capture_output=True)

    # The intermediate plaintext PEM and the original P12 are no longer needed.
    for tmp in (pem_path, raw_p12):
        tmp.unlink(missing_ok=True)
    return dst_p12


def task_slug(task: dict) -> str:
    """Stable R2/registry slug for a task: explicit ``slug`` or slugified app name."""
    return task.get("slug") or slugify_filename(task.get("app_name", ""))


def trigger_revalidate(revalidate_url: str, secret: str) -> bool:
    """Call the Vercel on-demand revalidation hook (shared-secret protected).

    Returns ``True`` when the hook accepted the request. Failures are logged and
    reported as ``False`` so callers can skip dependent steps (stale cleanup).
    """
    if not secret:
        print(
            "[warn] VERCEL_REVALIDATE_SECRET not set - skipping Vercel revalidation",
            file=sys.stderr,
        )
        return False
    separator = "&" if "?" in revalidate_url else "?"
    url = f"{revalidate_url}{separator}secret={secret}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            ok = 200 <= response.status < 300
    except (HTTPError, URLError, OSError) as e:
        print(f"[error] Vercel revalidation request failed: {e}", file=sys.stderr)
        return False
    if ok:
        print("[info] Vercel revalidation triggered")
    else:
        print(f"[error] Vercel revalidation returned HTTP {response.status}", file=sys.stderr)
    return ok


def extract_ipa_metadata(ipa_path: Path) -> tuple[str, Optional[str]]:
    """Read the *actual* bundle id and short version from a signed IPA.

    Parses the top-level ``Payload/<App>.app/Info.plist`` (binary or XML).

    Returns ``(bundle_id, version)`` where ``version`` prefers
    ``CFBundleShortVersionString`` and falls back to ``CFBundleVersion``.
    """
    info_re = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")
    with zipfile.ZipFile(ipa_path) as zf:
        info_names = [name for name in zf.namelist() if info_re.match(name)]
        if not info_names:
            raise ValueError(f"No app Info.plist found inside IPA: {ipa_path}")
        # Shortest path == the app bundle's own Info.plist (not a nested extension).
        info_name = min(info_names, key=len)
        with zf.open(info_name) as fh:
            plist = plistlib.load(fh)

    bundle_id = plist.get("CFBundleIdentifier")
    if not bundle_id:
        raise ValueError(f"CFBundleIdentifier missing in {info_name}")
    version = plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion")
    return str(bundle_id), str(version) if version is not None else None


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
    required_fields = ["task_name", "app_name", "bundle_id"]
    for field in required_fields:
        if not task.get(field):
            return False, f"Missing required field: {field}"

    # slug (R2/registry key) must be safe to use in an object key path segment
    slug = task.get("slug")
    if slug and not re.fullmatch(r"[A-Za-z0-9._-]+", str(slug)):
        return False, f"Invalid slug (allowed: A-Za-z0-9._-): {slug}"

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

    # icon_path is optional, but a repo-relative one needs a repo to resolve against.
    icon_path = task.get("icon_path")
    if icon_path is not None:
        if not isinstance(icon_path, str) or not icon_path.strip():
            return False, "icon_path must be a non-empty string"
        is_url = icon_path.startswith(("http://", "https://"))
        is_ipa = icon_path.strip() == app_icon.IPA_SCHEME
        if not is_url and not is_ipa and not has_repo_url:
            return False, "icon_path is repo-relative but no repo_url is set; use a full URL"

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


def publish_registry(
    store: r2_store.R2Store,
    updates: list[dict],
    processed_slugs: list[str],
    revalidate_url: str,
    revalidate_secret: str,
) -> bool:
    """Merge signing results into apps.json, revalidate Vercel, clean stale keys.

    Strict ordering (D7): new IPAs are already uploaded (immutable keys) ->
    merge + upload apps.json -> revalidate -> delete versioned keys the merged
    apps.json no longer references. Any failure skips the cleanup step.
    Returns ``False`` when any step failed.
    """
    failed = False
    try:
        current_doc = store.download_json(store.apps_json_key)
    except Exception as e:
        print(f"[error] Failed to read apps.json from R2: {e}", file=sys.stderr)
        return False

    merged_doc, changed = apps_registry.merge_registry_doc(current_doc, updates)

    revalidated = True
    if changed:
        try:
            store.upload_json(store.apps_json_key, merged_doc)
        except Exception as e:
            print(f"[error] Failed to upload apps.json to R2: {e}", file=sys.stderr)
            return False
        revalidated = trigger_revalidate(revalidate_url, revalidate_secret)
        if not revalidated:
            failed = True
    else:
        print("[info] apps.json already up to date - no revalidation needed")

    if revalidated:
        referenced = r2_store.referenced_keys_from_apps(store, merged_doc.get("apps", []))
        try:
            store.cleanup_stale(processed_slugs, referenced)
        except Exception as e:
            print(f"[error] Failed to clean stale R2 objects: {e}", file=sys.stderr)
            failed = True
    else:
        print("[warn] Skipping stale-object cleanup (revalidation did not complete)")

    return not failed


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
        "APPLE_DEV_CERT_P12_ENCODED",
        "APPLE_DEV_CERT_PASSWORD",
    ]
    for key in required_envs:
        if not os.getenv(key):
            print(f"[error] Missing required environment variable: {key}", file=sys.stderr)
            return 3

    cert_password = os.environ["APPLE_DEV_CERT_PASSWORD"]

    # R2 object storage (S3-compatible) holds every published artifact: the
    # versioned IPAs, per-app icons, and the site/apps.json registry.
    r2_cfg = cfg.get("r2", {}) or {}
    try:
        store = r2_store.R2Store.from_env(
            key_prefix=r2_cfg.get("key_prefix", "apps"),
            apps_json_key=r2_cfg.get("apps_json_key", "site/apps.json"),
        )
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3

    revalidate_url = os.getenv("VERCEL_REVALIDATE_URL", DEFAULT_REVALIDATE_URL)
    revalidate_secret = os.getenv("VERCEL_REVALIDATE_SECRET", "")

    zsign_bin = find_zsign()
    print(f"[info] Using zsign: {zsign_bin}")

    work_root = Path("work").resolve()
    work_root.mkdir(exist_ok=True)
    cache_dir = work_root / "cache"
    cache_dir.mkdir(exist_ok=True)

    # Decode the signing certificate once. Apple's P12 uses legacy RC2 encryption
    # that zsign's statically-linked OpenSSL can't read, so re-wrap it as a modern
    # AES P12. zsign then signs from the p12 directly — no Keychain / identity.
    try:
        p12_path = prepare_signing_p12(
            os.environ["APPLE_DEV_CERT_P12_ENCODED"], work_root, cert_password
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
        print(
            f"[error] Failed to normalise signing certificate (openssl): {stderr or e}",
            file=sys.stderr,
        )
        return 4

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

    # Per-app publish results merged into the R2 apps.json registry afterwards.
    registry_updates: list[dict] = []
    processed_slugs: list[str] = []

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

        # Use provisioning profile synced by sync_profiles_asc.py
        synced_profile = Path(f"work/profiles/{task_name}.mobileprovision")
        mobileprov_path = tdir / "profile.mobileprovision"

        if not synced_profile.exists():
            print(
                f"[task {i}] Provisioning profile not found: {synced_profile}\n"
                f"  Ensure sync_profiles_asc.py ran successfully and bundle_id is correct.",
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

        # Re-sign with zsign (p12 + provisioning profile -> fresh output IPA).
        signed_ipa = tdir / f"{safe_name}.ipa"
        zsign_argv = build_zsign_argv(
            zsign_bin,
            p12_path,
            cert_password,
            mobileprov_path,
            ori_ipa,
            signed_ipa,
            bundle_id=task["bundle_id"],
        )
        print(f"[task {i}] Re-signing with zsign -> {signed_ipa.name}")
        try:
            subprocess.run(zsign_argv, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] zsign re-sign failed: {e}", file=sys.stderr)
            any_fail = True
            continue

        if not signed_ipa.exists() or signed_ipa.stat().st_size == 0:
            print(f"[task {i}] Signed IPA missing or empty: {signed_ipa}", file=sys.stderr)
            any_fail = True
            continue

        # Read the app's ACTUAL bundle id / version from the signed IPA
        # (authoritative — independent of what the TOML declares). The version
        # also determines the immutable, versioned R2 object key.
        try:
            bundle_id_actual, version_actual = extract_ipa_metadata(signed_ipa)
        except Exception as e:
            # Fall back to the declared bundle id; keep going so the IPA stays usable.
            bundle_id_actual = task.get("bundle_id", "")
            version_actual = None
            print(
                f"[task {i}] Could not read IPA metadata ({e}); "
                f"falling back to declared bundle id '{bundle_id_actual}'",
                file=sys.stderr,
            )

        publish_version = version_actual or "1.0"
        slug = task_slug(task)

        # Upload the signed IPA to R2 under its versioned key (immutable).
        key = store.ipa_key(slug, publish_version, f"{safe_name}.ipa")
        try:
            ipa_url = store.upload_ipa(signed_ipa, key)
        except Exception as e:
            print(f"[task {i}] R2 upload failed: {e}", file=sys.stderr)
            any_fail = True
            continue

        print(
            f"[task {i}] Completed: {app_name} -> {ipa_url} "
            f"({bundle_id_actual} v{publish_version})"
        )

        # Refresh the card icon from the task's declared asset. Pinned to the
        # release tag so the icon tracks the build being published. A failure
        # here is non-fatal: the icon key keeps whatever it already held, and
        # the download page falls back to a lettered tile if it is empty.
        icon_url = store.public_url(store.icon_key(slug))
        icon_path = task.get("icon_path")
        if icon_path:
            try:
                png = app_icon.build_icon_png(
                    icon_path,
                    task.get("repo_url"),
                    ref=(version_info or {}).get("version"),
                    ipa_path=signed_ipa,
                )
                icon_url = store.upload_icon(slug, png)
            except Exception as e:
                print(
                    f"[task {i}] Icon refresh failed ({e}); keeping existing icon", file=sys.stderr
                )
        else:
            print(f"[task {i}] No icon_path configured; keeping existing icon")

        # The itms.plist is no longer uploaded — the Vercel front-end renders it
        # dynamically from apps.json. Queue the registry refresh for this app.
        registry_updates.append(
            {
                "slug": slug,
                "name": app_name,
                "bundleId": bundle_id_actual,
                "version": publish_version,
                "ipaUrl": ipa_url,
                "iconUrl": icon_url,
            }
        )
        processed_slugs.append(slug)

        # Update release cache ONLY after successful signing and upload
        if version_info:
            release_cache["tasks"][task_name] = version_info
            print(f"[task {i}] Updated version cache for: {task_name}")

    # Save release cache
    if github_client:
        save_release_cache(release_cache_path, release_cache)

    # Refresh the R2 apps.json registry, revalidate Vercel, and clean up stale
    # IPA versions — strictly in this order (see D7 in the migration plan).
    if registry_updates:
        if not publish_registry(
            store, registry_updates, processed_slugs, revalidate_url, revalidate_secret
        ):
            any_fail = True

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
