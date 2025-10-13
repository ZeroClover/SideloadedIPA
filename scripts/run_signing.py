#!/usr/bin/env python3
import base64
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Prefer Python 3.11+'s tomllib; fallback to tomli if needed
try:
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except Exception as e:
        print("tomllib/tomli not available. Install tomli or use Python 3.11+.", file=sys.stderr)
        raise e


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


def find_fastlane() -> str:
    """Return the fastlane executable name, error if not found in PATH."""
    from shutil import which

    exe = which("fastlane")
    if not exe:
        raise FileNotFoundError(
            "fastlane not found in PATH. Ensure it is installed on the runner."
        )
    return exe


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

    fastlane_bin = find_fastlane()
    print(f"[info] Using fastlane at: {fastlane_bin}")

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

    any_fail = False

    for i, task in enumerate(tasks, start=1):
        print("=" * 80)
        print(f"[task {i}] Starting: {task}")
        task_name = task.get("task_name")
        app_name = task.get("app_name")
        ipa_url = task.get("ipa_url")
        asset_server_path = task.get("asset_server_path")

        if not all([task_name, app_name, ipa_url, asset_server_path]):
            print(f"[task {i}] Missing required fields in task: {task}", file=sys.stderr)
            any_fail = True
            continue

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
                file=sys.stderr
            )
            any_fail = True
            continue

        print(f"[task {i}] Using synced profile: {synced_profile}")
        shutil.copy2(synced_profile, mobileprov_path)

        # Download IPA
        ori_ipa = tdir / f"{safe_name}_ori.ipa"
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
        # fastlane resign writes in-place unless bundle_id/version changes; ensure output path
        # We'll copy the result to signed_ipa afterwards if needed
        fl_cmd = f"{shlex.quote(fastlane_bin)} run resign " + " ".join(resign_params)
        try:
            run(fl_cmd)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] fastlane resign failed: {e}", file=sys.stderr)
            any_fail = True
            continue

        # Determine resulting IPA: fastlane resign may create a new ipa alongside or replace original.
        # If a new ipa with same name exists in current dir, prefer it; else use original as output.
        result_ipa: Path = ori_ipa
        # Common pattern: resigns to same path; if so, copy to signed_ipa for upload naming consistency
        try:
            if result_ipa.exists():
                if result_ipa.resolve() != signed_ipa.resolve():
                    # copy over to signed path
                    shutil.copy2(result_ipa, signed_ipa)
            else:
                # Fallback: search for any .ipa in tdir newer than original download
                latest_ipas = sorted(tdir.glob("*.ipa"), key=lambda p: p.stat().st_mtime, reverse=True)
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

        # Upload via scp (password-based as requested)
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

    print("=" * 80)
    if any_fail:
        print("[summary] Some tasks failed. See logs above.", file=sys.stderr)
        return 5
    print("[summary] All tasks completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
