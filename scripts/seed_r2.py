#!/usr/bin/env python3
"""One-time icon migration for the serverless migration (plan P1).

Copies each pipeline app's card icon from the legacy server
(``https://itms.zeroclover.io/<slug>/icon.png``) to R2
(``apps/<slug>/icon.png``), so the Vercel download page has icons from day one.

Scope per the plan's v3 decisions (§9): manual apps (EhPanda, Sonolus) are
retired — none of their assets are migrated. apps.json needs no seeding: the
first full CI build (P4) generates it on R2. Idempotent: re-running simply
overwrites the icons.

Run with the R2_* env vars set (see .env.example)::

    uv run python scripts/seed_r2.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import r2_store

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

OLD_BASE_URL = os.getenv("OLD_ASSETS_BASE_URL", "https://itms.zeroclover.io").rstrip("/")
ICON_CACHE_CONTROL = "public, max-age=300"


def load_pipeline_slugs(config_path: Path) -> list[str]:
    """Slugs of the apps the signing pipeline manages (icon migration list)."""
    with config_path.open("rb") as f:
        cfg = tomllib.load(f)
    slugs = []
    for task in cfg.get("tasks", []):
        slug = task.get("slug") or task.get("app_name", "")
        if slug:
            slugs.append(slug)
    return slugs


def fetch_bytes(url: str) -> bytes | None:
    """GET ``url``; returns the body, or ``None`` on 404. Other errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": "r2-icon-seed/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def seed_icon(store: r2_store.R2Store, slug: str, work_dir: Path) -> bool:
    """Copy one app's card icon from the legacy server to R2 (best effort)."""
    data = fetch_bytes(f"{OLD_BASE_URL}/{slug}/icon.png")
    if not data:
        print(f"[warn] No icon on legacy server for {slug} - skipping", file=sys.stderr)
        return False
    local = work_dir / f"{slug}-icon.png"
    local.write_bytes(data)
    store.upload_file(
        local,
        store.icon_key(slug),
        content_type="image/png",
        cache_control=ICON_CACHE_CONTROL,
    )
    return True


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    config_path = Path(os.getenv("CONFIG_TOML", repo_root / "configs" / "tasks.toml"))

    try:
        store = r2_store.R2Store.from_env()
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3

    slugs = load_pipeline_slugs(config_path)
    if not slugs:
        print(f"[error] No pipeline tasks found in {config_path}", file=sys.stderr)
        return 2
    print(f"[info] Migrating icons for {len(slugs)} pipeline app(s): {', '.join(slugs)}")

    migrated = 0
    with tempfile.TemporaryDirectory(prefix="r2-icon-seed-") as tmp:
        work_dir = Path(tmp)
        for slug in slugs:
            if seed_icon(store, slug, work_dir):
                migrated += 1

    print(f"[summary] Icons migrated: {migrated}/{len(slugs)}")
    return 0 if migrated == len(slugs) else 1


if __name__ == "__main__":
    sys.exit(main())
