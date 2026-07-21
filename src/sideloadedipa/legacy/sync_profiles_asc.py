#!/usr/bin/env python3
"""Sync development provisioning profiles using App Store Connect CLI."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE_TYPE = "IOS_APP_DEVELOPMENT"
DEVICE_PLATFORM = "IOS"
COMPATIBLE_DEVICE_CLASSES = {"IPHONE", "IPAD"}
WORK_DIR = Path(__file__).parent.parent / "work"
PROFILES_DIR = WORK_DIR / "profiles"
CACHE_DIR = WORK_DIR / "cache"
CACHE_OLD_DIR = WORK_DIR / "cache-old"


def run_asc(args: list[str], check: bool = True) -> dict[str, Any] | None:
    """Run asc CLI command and return JSON output."""
    cmd = ["asc"] + args + ["--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if check and result.returncode != 0:
        print(f"[error] Command failed: {' '.join(cmd)}", file=sys.stderr)
        print(f"[error] {result.stderr}", file=sys.stderr)
        sys.exit(1)

    if result.stdout.strip():
        return json.loads(result.stdout)
    return None


def load_tasks() -> list[dict[str, str]]:
    """Load tasks from TOML config."""
    import tomllib

    config_path = os.getenv("CONFIG_TOML", "configs/tasks.toml")
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    tasks = [task for task in config.get("tasks", []) if task.get("publication_enabled", True)]
    if not tasks:
        raise ValueError(f"No tasks defined in {config_path}")

    for task in tasks:
        if "bundle_id" not in task:
            raise ValueError(f"Task missing bundle_id: {task}")

    return tasks


def fetch_devices() -> list[dict[str, Any]]:
    """Fetch all enabled iOS devices."""
    print("[info] Fetching iOS devices...")
    result = run_asc(
        ["devices", "list", "--platform", DEVICE_PLATFORM, "--status", "ENABLED", "--paginate"]
    )
    devices = result.get("data", [])
    print(f"[info] Found {len(devices)} iOS devices")
    return devices


def fetch_certificates() -> list[dict[str, Any]]:
    """Fetch development certificates."""
    print("[info] Fetching development certificates...")
    result = run_asc(
        ["certificates", "list", "--certificate-type", "IOS_DEVELOPMENT,DEVELOPMENT", "--paginate"]
    )
    certs = result.get("data", [])
    print(f"[info] Found {len(certs)} development certificates")
    return certs


def find_bundle_id(identifier: str) -> str | None:
    """Find bundle ID resource by identifier."""
    print(f"[info] Looking up Bundle ID: {identifier}")
    result = run_asc(["bundle-ids", "list", "--paginate"])

    for bundle in result.get("data", []):
        if bundle.get("attributes", {}).get("identifier") == identifier:
            return bundle["id"]

    return None


def find_profile(name: str, bundle_id: str) -> dict[str, Any] | None:
    """Find profile by name and bundle ID."""
    print(f"[info] Checking for existing profile: {name}")
    result = run_asc(["profiles", "list", "--profile-type", PROFILE_TYPE, "--paginate"])

    for profile in result.get("data", []):
        if profile.get("attributes", {}).get("name") == name:
            rel_bundle = profile.get("relationships", {}).get("bundleId", {}).get("data", {})
            rel_bundle_id = rel_bundle.get("id")

            # Fallback when list payload omits relationship linkage data.
            if not rel_bundle_id:
                rel_bundle_id = get_profile_bundle_id(profile.get("id", ""))

            if rel_bundle_id == bundle_id:
                return profile

    return None


def get_profile_bundle_id(profile_id: str) -> str | None:
    """Fetch bundle ID relationship for a profile."""
    if not profile_id:
        return None

    result = run_asc(["profiles", "links", "bundle-id", "--id", profile_id])
    data = (result or {}).get("data", {})
    if isinstance(data, dict):
        return data.get("id")
    return None


def extract_compatible_device_ids(devices: list[dict[str, Any]]) -> list[str]:
    """Return device IDs compatible with iOS development provisioning profiles."""
    compatible_ids: list[str] = []
    excluded = 0

    for device in devices:
        device_id = device.get("id")
        device_class = device.get("attributes", {}).get("deviceClass")

        if not device_id:
            continue

        if device_class in COMPATIBLE_DEVICE_CLASSES:
            compatible_ids.append(device_id)
        else:
            excluded += 1

    print(
        f"[info] Using {len(compatible_ids)} compatible devices"
        f" ({excluded} excluded by device class)"
    )

    if not compatible_ids:
        raise RuntimeError("No compatible devices found (expected IPHONE or IPAD)")

    return compatible_ids


def create_profile(name: str, bundle_id: str, cert_ids: list[str], device_ids: list[str]) -> str:
    """Create a new provisioning profile."""
    print(f"[info] Creating new profile: {name}")

    cmd = [
        "profiles",
        "create",
        "--name",
        name,
        "--profile-type",
        PROFILE_TYPE,
        "--bundle",
        bundle_id,
        "--certificate",
        ",".join(cert_ids),
        "--device",
        ",".join(device_ids),
    ]

    result = run_asc(cmd)
    profile_id = result["data"]["id"]
    print(f"[info] Profile created successfully: {profile_id}")
    return profile_id


def delete_profile(profile_id: str):
    """Delete a provisioning profile."""
    print(f"[info] Deleting old profile: {profile_id}")
    run_asc(["profiles", "delete", "--id", profile_id, "--confirm"])


def download_profile(profile_id: str, output_path: Path):
    """Download provisioning profile."""
    print(f"[info] Downloading profile to: {output_path}")

    # Get profile with content
    result = run_asc(["profiles", "view", "--id", profile_id])
    profile_content = result["data"]["attributes"]["profileContent"]

    # Decode base64 and write
    import base64

    decoded = base64.b64decode(profile_content)
    output_path.write_bytes(decoded)
    print(f"[info] Profile downloaded: {output_path}")


def calculate_device_checksum(devices: list[dict[str, Any]]) -> str:
    """Calculate checksum for device list."""
    normalized = sorted(devices, key=lambda d: d["id"])
    json_str = json.dumps(normalized, separators=(",", ":"))
    digest = hashlib.sha256(json_str.encode()).hexdigest()
    return f"sha256:{digest}"


def save_device_list_cache(devices: list[dict[str, Any]]):
    """Save device list to cache."""
    print("[info] Saving device list cache...")

    device_data = [
        {
            "id": d["id"],
            "name": d["attributes"]["name"],
            "platform": d["attributes"]["platform"],
            "device_class": d["attributes"]["deviceClass"],
            "udid": d["attributes"]["udid"],
            "status": d["attributes"]["status"],
        }
        for d in devices
    ]

    checksum = calculate_device_checksum(device_data)
    cache_data = {
        "devices": device_data,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "checksum": checksum,
    }

    cache_path = CACHE_DIR / "device-list.json"
    cache_path.write_text(json.dumps(cache_data, indent=2))
    print(f"[info] Device list cache saved: {cache_path}")
    print(f"[info] Checksum: {checksum}")


def compare_cached_device_lists() -> bool:
    """Compare cached and current device lists."""
    cached_path = CACHE_OLD_DIR / "device-list.json"
    current_path = CACHE_DIR / "device-list.json"

    if not cached_path.exists():
        print("[info] No cached device list found - first run, devices considered changed")
        return True

    if not current_path.exists():
        print("[error] No current device list found - devices considered changed")
        return True

    cached = json.loads(cached_path.read_text())
    current = json.loads(current_path.read_text())

    cached_checksum = cached.get("checksum") or calculate_device_checksum(cached.get("devices", []))
    current_checksum = current.get("checksum") or calculate_device_checksum(
        current.get("devices", [])
    )

    if cached_checksum != current_checksum:
        print("[info] Device list changed:")
        print(f"  Cached checksum:  {cached_checksum}")
        print(f"  Current checksum: {current_checksum}")

        cached_devices = {d["id"]: d for d in cached.get("devices", [])}
        current_devices = {d["id"]: d for d in current.get("devices", [])}

        added = set(current_devices.keys()) - set(cached_devices.keys())
        removed = set(cached_devices.keys()) - set(current_devices.keys())

        if added:
            print(f"  → {len(added)} device(s) added:")
            for device_id in sorted(added):
                d = current_devices[device_id]
                print(f"     + {d['name']} ({d['device_class']})")

        if removed:
            print(f"  → {len(removed)} device(s) removed:")
            for device_id in sorted(removed):
                d = cached_devices[device_id]
                print(f"     - {d['name']} ({d['device_class']})")

        return True

    print("[info] Device list unchanged")
    return False


def write_github_output(key: str, value: str):
    """Write output to GitHub Actions."""
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"{key}={value}\n")


def check_entitlements():
    """Check if all profiles exist."""
    tasks = load_tasks()
    devices = fetch_devices()

    save_device_list_cache(devices)
    devices_changed = compare_cached_device_lists()

    missing = []
    for task in tasks:
        bundle_id = task["bundle_id"]
        app_name = task["app_name"]
        task_name = task["task_name"]
        profile_name = f"{app_name} Dev"

        print("=" * 80)
        print(f"[task] Checking profile for {app_name} ({bundle_id})")

        bundle_id_resource = find_bundle_id(bundle_id)
        if not bundle_id_resource:
            print(f"[error] Bundle ID '{bundle_id}' not found in App Store Connect")
            sys.exit(1)

        profile = find_profile(profile_name, bundle_id_resource)
        if profile:
            print(f"[info] Found profile: {profile_name}")
        else:
            print(f"[warn] Missing profile: {profile_name} (task: {task_name})")
            missing.append(task_name)

    write_github_output("devices_changed", "true" if devices_changed else "false")
    write_github_output("all_profiles_present", "true" if not missing else "false")
    write_github_output("missing_profiles", json.dumps(missing))

    print("[summary] Entitlements profile check completed")
    print(f"[summary] Devices changed: {'yes' if devices_changed else 'no'}")
    print(f"[summary] Missing profiles: {len(missing)}")


def sync_all():
    """Sync all provisioning profiles."""
    tasks = load_tasks()

    # Filter tasks if needed
    rebuild_all = os.getenv("REBUILD_ALL", "").lower() in ["1", "true", "yes", "on"]
    rebuild_tasks_json = os.getenv("REBUILD_TASKS", "")

    if not rebuild_all and rebuild_tasks_json:
        try:
            rebuild_tasks = json.loads(rebuild_tasks_json)
            if isinstance(rebuild_tasks, list):
                task_names = set(rebuild_tasks)
                tasks = [t for t in tasks if t["task_name"] in task_names]
                print(f"[info] Filtered to {len(tasks)} tasks from REBUILD_TASKS")
        except json.JSONDecodeError as e:
            print(f"[warn] Invalid REBUILD_TASKS JSON: {e}")

    if not tasks:
        print("[info] No tasks selected for profile sync - skipping")
        print("[summary] Profile sync completed")
        return

    skip_regeneration = os.getenv("SKIP_PROFILE_REGENERATION", "").lower() == "true"

    if skip_regeneration:
        print("[info] SKIP_PROFILE_REGENERATION=true - downloading existing profiles only")
        for task in tasks:
            download_existing_profile(task)
    else:
        devices = fetch_devices()
        save_device_list_cache(devices)

        print("[info] Regenerating all provisioning profiles")
        certificates = fetch_certificates()

        cert_ids = [c["id"] for c in certificates]
        device_ids = extract_compatible_device_ids(devices)

        for task in tasks:
            sync_profile(task, cert_ids, device_ids)

    print("[summary] Profile sync completed")


def sync_profile(task: dict[str, str], cert_ids: list[str], device_ids: list[str]):
    """Sync a single profile."""
    bundle_id = task["bundle_id"]
    app_name = task["app_name"]
    task_name = task["task_name"]
    profile_name = f"{app_name} Dev"

    print("=" * 80)
    print(f"[task] Syncing profile for {app_name} ({bundle_id})")

    bundle_id_resource = find_bundle_id(bundle_id)
    if not bundle_id_resource:
        print(f"[error] Bundle ID '{bundle_id}' not found")
        sys.exit(1)

    existing_profile = find_profile(profile_name, bundle_id_resource)

    if existing_profile:
        delete_profile(existing_profile["id"])

    profile_id = create_profile(profile_name, bundle_id_resource, cert_ids, device_ids)
    output_path = PROFILES_DIR / f"{task_name}.mobileprovision"
    download_profile(profile_id, output_path)


def download_existing_profile(task: dict[str, str]):
    """Download existing profile or create if missing."""
    bundle_id = task["bundle_id"]
    app_name = task["app_name"]
    task_name = task["task_name"]
    profile_name = f"{app_name} Dev"

    print("=" * 80)
    print(f"[task] Downloading existing profile for {app_name} ({bundle_id})")

    bundle_id_resource = find_bundle_id(bundle_id)
    if not bundle_id_resource:
        print(f"[error] Bundle ID '{bundle_id}' not found")
        sys.exit(1)

    existing_profile = find_profile(profile_name, bundle_id_resource)

    if existing_profile:
        print(f"[info] Found existing profile: {profile_name}")
        output_path = PROFILES_DIR / f"{task_name}.mobileprovision"
        download_profile(existing_profile["id"], output_path)
    else:
        print(f"[warn] No existing profile found for {profile_name}")
        print(f"[info] Creating missing profile for new task: {task_name}")

        devices = fetch_devices()
        certificates = fetch_certificates()

        cert_ids = [c["id"] for c in certificates]
        device_ids = extract_compatible_device_ids(devices)

        profile_id = create_profile(profile_name, bundle_id_resource, cert_ids, device_ids)
        output_path = PROFILES_DIR / f"{task_name}.mobileprovision"
        download_profile(profile_id, output_path)


def main():
    """Main entry point."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_OLD_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check_entitlements()
    else:
        sync_all()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
        if os.getenv("DEBUG", "").lower() == "true":
            import traceback

            traceback.print_exc()
        sys.exit(1)
