#!/usr/bin/env python3
"""Bring every published icon in line with the current key + header policy.

Idempotent and safe to re-run: for each apps.json entry it reads the icon the
entry names, re-uploads it through ``upload_icon`` — which recomputes the
content-addressed key and stamps the current headers — and rewrites the entry
if the key moved. Re-running with nothing to do is a no-op that reports so.

It exists because R2 stores headers on the object at PUT time, so a change to
``ICON_CACHE_CONTROL`` reaches live objects only when they are uploaded again.
The signing pipeline does that for a task on its next rebuild, which for a
stable app can be a long wait; this applies the policy to everything at once.

Two rounds of policy have needed it so far:

- icons moved off the mutable ``apps/<slug>/icon.png`` onto content-addressed
  keys, so a refresh is visible immediately rather than after the zone's 4-hour
  browser TTL (the pipeline holds no Cloudflare API token to purge with);
- icons gained ``no-transform``, which opts them out of Cloudflare Polish's
  lossy re-encoding.

Legacy ``icon.png`` objects are left in place — the next signing run for that
slug retires them through ``cleanup_stale``, the same path that retires
superseded icons. Pass ``--delete-legacy`` to remove them straight away.

Run from an environment carrying the R2_* variables::

    python scripts/reconcile_icons.py            # dry run, prints the plan
    python scripts/reconcile_icons.py --apply
"""

from __future__ import annotations

import argparse
import datetime
import sys
from typing import Any

import r2_store


def reconcile(store: r2_store.R2Store, apply: bool, delete_legacy: bool) -> int:
    doc = store.download_json(store.apps_json_key)
    if not doc:
        print("[error] apps.json not found on R2", file=sys.stderr)
        return 1

    apps: list[dict[str, Any]] = doc.get("apps") or []
    legacy_keys: list[str] = []
    rekeyed = 0
    refreshed = 0

    for app in apps:
        slug = app.get("slug") or ""
        icon_url = app.get("iconUrl") or ""
        if not slug or not icon_url:
            print(f"[skip] {slug or '(no slug)'}: no iconUrl")
            continue

        key = store.key_from_url(icon_url)
        if not key:
            # A manual entry whose icon is hosted somewhere other than our bucket.
            print(f"[skip] {slug}: iconUrl is not on this bucket ({icon_url})")
            continue

        try:
            png = store.download_bytes(key)
        except Exception as e:
            # Abort rather than write a half-reconciled apps.json.
            print(f"[error] {slug}: cannot read {key}: {e}", file=sys.stderr)
            return 1

        new_key = store.icon_key(slug, png)
        if new_key == key:
            print(f"[plan] {slug}: re-upload {key} to refresh headers ({len(png)} bytes)")
            refreshed += 1
        else:
            print(f"[plan] {slug}: {key} -> {new_key} ({len(png)} bytes)")
            rekeyed += 1
            if key == store.legacy_icon_key(slug):
                legacy_keys.append(key)

        if apply:
            store.upload_icon(slug, png)
        app["iconUrl"] = store.public_url(new_key)

    if not rekeyed and not refreshed:
        print("[info] Nothing to reconcile.")
        return 0

    if not apply:
        print(f"[info] Dry run — {rekeyed} to re-key, {refreshed} to re-header. Use --apply.")
        return 0

    # apps.json only needs rewriting when a key actually moved; a header-only
    # refresh leaves every URL exactly as it was.
    if rekeyed:
        doc["apps"] = apps
        doc["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        store.upload_json(store.apps_json_key, doc)
        print(f"[info] Rewrote {rekeyed} iconUrl(s) in apps.json.")
    else:
        print(f"[info] Re-uploaded {refreshed} icon(s) in place; apps.json unchanged.")

    if delete_legacy and legacy_keys:
        store.delete_keys(legacy_keys)
    elif legacy_keys:
        print("[info] Legacy icon.png objects left for cleanup_stale to retire.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help="also delete any old apps/<slug>/icon.png objects (requires --apply)",
    )
    args = parser.parse_args()
    if args.delete_legacy and not args.apply:
        parser.error("--delete-legacy requires --apply")

    return reconcile(r2_store.R2Store.from_env(), args.apply, args.delete_legacy)


if __name__ == "__main__":
    sys.exit(main())
