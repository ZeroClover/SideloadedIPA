#!/usr/bin/env python3
"""Maintain ``site/apps.json`` on R2 — the single data source for page + plist.

apps.json lives ONLY on R2 (never in git). The Vercel-hosted download page and
the dynamic itms.plist route both read it; the signing pipeline owns it
outright: every entry is generated from pipeline tasks (decision in the
migration plan §9 — no manual entries are kept; the initial document is
created by the first full CI build). Structure::

    {
      "updatedAt": "2026-07-18T04:00:00Z",
      "apps": [
        {
          "slug": "ehpanda",        # stable key; matches the R2 object layout
          "name": "EhPanda",        # display name (task app_name)
          "bundleId": "io.zeroclover.app.ehpanda",
          "version": "2.7.4",
          "ipaUrl": "https://ipa.zeroclover.io/apps/ehpanda/2.7.4/EhPanda.ipa",
          "iconUrl": "https://ipa.zeroclover.io/apps/ehpanda/icon-1f3a9c2b7d04.png"
        }
      ]
    }

Merge semantics (per plan v3):

- existing entries (matched by ``slug``) have ``name`` / ``bundleId`` /
  ``version`` / ``ipaUrl`` / ``iconUrl`` refreshed from the task result;
- updates whose ``slug`` is not present are appended as new entries;
- entries the pipeline does not mention are left as they were.

Only truthy update values overwrite, which is load-bearing for the
content-addressed icon keys: a task with no ``icon_path`` (or whose icon fetch
failed) reports an empty ``iconUrl``, and the entry must keep pointing at the
hashed key it already has rather than losing its icon.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

# Fields the pipeline refreshes on an existing entry (name always = app_name).
_REFRESH_FIELDS = ("name", "bundleId", "version", "ipaUrl", "iconUrl")

# Field order used when appending a brand-new entry.
_ENTRY_FIELDS = ("slug", "name", "bundleId", "version", "ipaUrl", "iconUrl")

# Registry entries are plain JSON dicts (slug/name/bundleId/version/ipaUrl/iconUrl).
Entry = dict[str, Any]


def merge_apps(
    existing: list[Entry],
    updates: list[Entry],
) -> tuple[list[Entry], bool]:
    """Merge signing results into the apps.json entry list.

    Returns ``(merged_list, changed)``. ``existing`` entries are dicts with the
    apps.json shape; ``updates`` carry the fields the pipeline observed for the
    task (slug, name, bundleId, version, ipaUrl, iconUrl).
    """
    updates_by_slug = {u["slug"]: u for u in updates if u.get("slug")}
    changed = False
    result: list[Entry] = []
    seen: set[str] = set()

    for app in existing:
        merged = dict(app)
        slug = merged.get("slug", "")
        seen.add(slug)
        update = updates_by_slug.get(slug)
        if update:
            # Refresh every pipeline-owned field (name always = task app_name).
            for key in _REFRESH_FIELDS:
                new_value = update.get(key)
                if new_value and merged.get(key) != new_value:
                    merged[key] = new_value
                    changed = True
        result.append(merged)

    for update in updates:
        slug = update.get("slug", "")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        result.append({field: update.get(field, "") for field in _ENTRY_FIELDS})
        changed = True

    return result, changed


def merge_registry_doc(
    existing_doc: Optional[dict[str, Any]],
    updates: list[Entry],
    now: Optional[datetime.datetime] = None,
) -> tuple[dict[str, Any], bool]:
    """Merge updates into a full apps.json document.

    ``existing_doc`` may be ``None`` (apps.json missing on R2 — first run),
    which bootstraps an empty registry. When anything changes, ``updatedAt``
    is stamped with ``now`` (UTC). Returns ``(merged_doc, changed)``.
    """
    existing_apps = (existing_doc or {}).get("apps") or []
    merged_apps, changed = merge_apps(existing_apps, updates)
    if not changed:
        return {"updatedAt": (existing_doc or {}).get("updatedAt"), "apps": existing_apps}, False

    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"updatedAt": stamp, "apps": merged_apps}, True
