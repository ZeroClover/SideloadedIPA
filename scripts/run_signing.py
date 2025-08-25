#!/usr/bin/env python3
import base64
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

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


def find_zsign() -> Path:
    # Prefer local ./zsign
    here = Path.cwd()
    candidate = here / "zsign"
    if candidate.exists():
        return candidate
    # Fallback: search within repo
    for p in here.rglob("zsign"):
        if p.is_file():
            return p
    raise FileNotFoundError("zsign binary not found. Ensure scripts/prepare_env.sh ran successfully.")


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

    apple_pwd = os.environ["APPLE_DEV_CERT_PASSWORD"]
    assets_ip = os.environ["ASSETS_SERVER_IP"]
    assets_user = os.environ["ASSETS_SERVER_USER"]
    assets_pass = os.environ["ASSETS_SERVER_CREDENTIALS"]

    zsign_path = find_zsign()
    print(f"[info] Using zsign at: {zsign_path}")

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

        # MobileProvision env var (Base64)
        env_key = f"{str(task_name).upper()}_MOBILEPROVISION"
        mp_b64 = os.getenv(env_key)

        # Fallback: file-based mobile provisioning if env not set
        if not mp_b64:
            fallback_file = Path(f"configs/mobileprovision/{str(task_name).upper()}.mobileprovision.b64")
            if fallback_file.exists():
                mp_b64 = fallback_file.read_text(encoding="utf-8")
                print(f"[task {i}] Using fallback mobileprovision file: {fallback_file}")

        if not ent_b64:
                print(f"[task {i}] Missing mobileprovision base64 in env '{env_key}' and no fallback file found", file=sys.stderr)
            any_fail = True
            continue

        mobileprov_path = tdir / "profile.mobileprovision"
        try:
            if not mp_b64:
                print(f"[task {i}] Missing mobileprovision base64 in env '{env_key}' and no fallback file found", file=sys.stderr)
                any_fail = True
                continue
            decode_b64_to_file(mp_b64.strip(), mobileprov_path)
        except Exception as e:
            print(f"[task {i}] Failed to decode mobileprovision: {e}", file=sys.stderr)
            any_fail = True
            continue

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

        # Sign with zsign
        signed_ipa = tdir / f"{safe_name}.ipa"
        z_cmd = (
            f"{shlex.quote(str(zsign_path))} "
            f"-k apple_dev.p12 "
            f"-p {shlex.quote(apple_pwd)} "
            f"-m {shlex.quote(str(mobileprov_path))} "
            f"-o {shlex.quote(str(signed_ipa))} "
            f"{shlex.quote(str(ori_ipa))}"
        )
        try:
            run(z_cmd)
        except subprocess.CalledProcessError as e:
            print(f"[task {i}] zsign failed: {e}", file=sys.stderr)
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
