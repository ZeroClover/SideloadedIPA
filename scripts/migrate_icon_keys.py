#!/usr/bin/env python3
"""One-time migration: stable icon keys -> content-addressed icon keys.

Icons used to live at a mutable ``apps/<slug>/icon.png``. Cloudflare's
zone-level 4-hour browser TTL overrides the origin's ``max-age``, so a
refreshed icon stayed invisible for hours and the pipeline has no API token to
purge with. Icons are now uploaded to ``apps/<slug>/icon-<sha12>.png`` with
immutable headers instead (see ``r2_store.icon_key``).

This script re-keys the icons that predate that change: for every apps.json
entry whose ``iconUrl`` still points at a legacy key, it downloads the object,
re-uploads it under its content-addressed key, and rewrites the entry. The
legacy objects are left in place — the next signing run for that slug deletes
them through ``cleanup_stale``, which is the same path that retires superseded
icons from now on. Pass ``--delete-legacy`` to remove them immediately.

Run it once, from an environment carrying the R2_* variables::

    python scripts/migrate_icon_keys.py            # dry run, prints the plan
    python scripts/migrate_icon_keys.py --apply
"""

from __future__ import annotations

import argparse
import datetime
import sys
from typing import Any

import r2_store


def migrate(store: r2_store.R2Store, apply: bool, delete_legacy: bool) -> int:
    doc = store.download_json(store.apps_json_key)
    if not doc:
        print("[error] apps.json not found on R2", file=sys.stderr)
        return 1

    apps: list[dict[str, Any]] = doc.get("apps") or []
    legacy_keys: list[str] = []
    changed = False

    for app in apps:
        slug = app.get("slug") or ""
        icon_url = app.get("iconUrl") or ""
        if not slug or not icon_url:
            print(f"[skip] {slug or '(no slug)'}: no iconUrl")
            continue

        key = store.key_from_url(icon_url)
        if key != store.legacy_icon_key(slug):
            print(f"[skip] {slug}: iconUrl is not a legacy key ({icon_url})")
            continue

        try:
            png = store.download_bytes(key)
        except Exception as e:
            print(f"[error] {slug}: cannot read {key}: {e}", file=sys.stderr)
            return 1

        new_key = store.icon_key(slug, png)
        print(f"[plan] {slug}: {key} -> {new_key} ({len(png)} bytes)")
        if apply:
            store.upload_icon(slug, png)
        app["iconUrl"] = store.public_url(new_key)
        legacy_keys.append(key)
        changed = True

    if not changed:
        print("[info] Nothing to migrate.")
        return 0

    if not apply:
        print("[info] Dry run — re-run with --apply to write apps.json.")
        return 0

    doc["apps"] = apps
    doc["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.upload_json(store.apps_json_key, doc)
    print(f"[info] Rewrote {len(legacy_keys)} iconUrl(s) in apps.json.")

    if delete_legacy:
        store.delete_keys(legacy_keys)
    else:
        print("[info] Legacy icon.png objects left for cleanup_stale to retire.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help="also delete the old apps/<slug>/icon.png objects (implies --apply)",
    )
    args = parser.parse_args()
    if args.delete_legacy and not args.apply:
        parser.error("--delete-legacy requires --apply")

    return migrate(r2_store.R2Store.from_env(), args.apply, args.delete_legacy)


if __name__ == "__main__":
    sys.exit(main())
