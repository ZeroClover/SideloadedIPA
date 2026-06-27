#!/usr/bin/env python3
"""
Update the ITMS download page (``site/``) from signing-pipeline results.

The download page's single source of truth is ``site/apps.js`` — a tiny JS file
that assigns ``window.ZC_APPS`` to an array of ``{ name, dir, bundleId, version }``
records (see ``site/README.md``). After each signing run, the pipeline knows the
*actual* bundle id and version of every app it just (re)signed; this module merges
those results back into ``apps.js``:

- existing apps (matched by ``dir``) have their ``version`` / ``bundleId`` refreshed
  while keeping their curated display ``name``;
- apps not yet listed are appended as new cards.

Whenever ``apps.js`` changes, the ``apps.js?v=N`` query in ``index.html`` is bumped
so Cloudflare's filename-based edge cache serves the fresh file (see the caching
note in ``site/README.md``).

It can also be used standalone to apply updates from a JSON file::

    python scripts/site_update.py site updates.json

where ``updates.json`` is a list of ``{name, dir, bundleId, version}`` objects.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

# Fields tracked per app card, in render order.
_FIELDS = ("name", "dir", "bundleId", "version")

# Matches the whole ``window.ZC_APPS = [ ... ];`` assignment.
#   group(1) = "window.ZC_APPS = "   group(2) = ";"
_ARRAY_RE = re.compile(r"(window\.ZC_APPS\s*=\s*)\[.*?\](\s*;)", re.DOTALL)

# Matches a single ``key: 'value'`` pair inside an object literal.
_KV_RE = re.compile(r"(\w+)\s*:\s*'([^']*)'")


def parse_apps_js(content: str) -> list[dict[str, str]]:
    """Parse the ``ZC_APPS`` array literal into a list of dicts.

    Tolerant of field order and extra whitespace; only single-quoted string
    values are supported (which is the format ``apps.js`` uses).
    """
    match = _ARRAY_RE.search(content)
    if not match:
        raise ValueError("window.ZC_APPS array literal not found in apps.js")

    entries: list[dict[str, str]] = []
    # Iterate object literals within the matched assignment.
    for obj in re.finditer(r"\{([^{}]*)\}", match.group(0)):
        fields = {key: value for key, value in _KV_RE.findall(obj.group(1))}
        if fields:
            entries.append(fields)
    return entries


def merge_apps(
    existing: list[dict[str, str]],
    updates: list[dict[str, str]],
) -> tuple[list[dict[str, str]], bool]:
    """Merge signing results into the existing app list.

    Existing entries (matched by ``dir``) keep their curated ``name`` but adopt the
    freshly observed ``bundleId`` / ``version``. Updates whose ``dir`` is not present
    are appended as new cards, preserving the original ordering of existing entries.

    Returns ``(merged_list, changed)``.
    """
    updates_by_dir = {u["dir"]: u for u in updates if u.get("dir")}
    changed = False
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    for app in existing:
        merged = dict(app)
        directory = merged.get("dir", "")
        seen.add(directory)
        update = updates_by_dir.get(directory)
        if update:
            # Refresh factual fields; keep the curated display name as-is.
            for key in ("bundleId", "version"):
                new_value = update.get(key)
                if new_value and merged.get(key) != new_value:
                    merged[key] = new_value
                    changed = True
        result.append(merged)

    for update in updates:
        directory = update.get("dir", "")
        if not directory or directory in seen:
            continue
        seen.add(directory)
        result.append(
            {
                "name": update.get("name", directory),
                "dir": directory,
                "bundleId": update.get("bundleId", ""),
                "version": update.get("version", ""),
            }
        )
        changed = True

    return result, changed


def render_apps_array(apps: list[dict[str, str]]) -> str:
    """Render the ``[ ... ]`` array body with aligned columns matching apps.js style."""
    rows: list[dict[str, str]] = []
    for app in apps:
        rows.append(
            {
                "name": f"'{app.get('name', '')}',",
                "dir": f"'{app.get('dir', '')}',",
                "bundleId": f"'{app.get('bundleId', '')}',",
                "version": f"'{app.get('version', '')}'",
            }
        )

    widths = {field: max((len(r[field]) for r in rows), default=0) for field in _FIELDS}

    lines = []
    for row in rows:
        lines.append(
            "  {{ name: {name} dir: {dir} bundleId: {bundleId} version: {version} }},".format(
                name=row["name"].ljust(widths["name"]),
                dir=row["dir"].ljust(widths["dir"]),
                bundleId=row["bundleId"].ljust(widths["bundleId"]),
                version=row["version"].ljust(widths["version"]),
            )
        )

    return "[\n" + "\n".join(lines) + "\n]"


def update_apps_js_content(
    content: str,
    updates: list[dict[str, str]],
) -> tuple[str, bool]:
    """Return ``(new_content, changed)`` after merging updates into apps.js text.

    Preserves the file header comment and everything outside the array literal.
    """
    match = _ARRAY_RE.search(content)
    if not match:
        raise ValueError("window.ZC_APPS array literal not found in apps.js")

    existing = parse_apps_js(content)
    merged, changed = merge_apps(existing, updates)
    if not changed:
        return content, False

    new_assignment = match.group(1) + render_apps_array(merged) + match.group(2)
    new_content = content[: match.start()] + new_assignment + content[match.end() :]
    return new_content, True


def bump_index_asset_version(html: str, asset: str = "apps.js") -> tuple[str, bool]:
    """Increment the ``<asset>?v=N`` cache-busting query in index.html.

    Returns ``(new_html, changed)``.
    """
    pattern = re.compile(rf"({re.escape(asset)}\?v=)(\d+)")
    changed = False

    def _repl(m: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{m.group(1)}{int(m.group(2)) + 1}"

    return pattern.sub(_repl, html), changed


def apply_site_updates(site_dir: Path, updates: list[dict[str, str]]) -> bool:
    """Apply signing results to ``apps.js`` (+ bump ``index.html``) on disk.

    Returns ``True`` if ``apps.js`` changed (and was rewritten), ``False`` otherwise.
    """
    apps_js = site_dir / "apps.js"
    index_html = site_dir / "index.html"

    content = apps_js.read_text(encoding="utf-8")
    new_content, changed = update_apps_js_content(content, updates)
    if not changed:
        return False

    apps_js.write_text(new_content, encoding="utf-8")
    print(f"[info] Updated download page data: {apps_js}")

    if index_html.exists():
        html = index_html.read_text(encoding="utf-8")
        new_html, bumped = bump_index_asset_version(html, "apps.js")
        if bumped:
            index_html.write_text(new_html, encoding="utf-8")
            print(f"[info] Bumped apps.js cache-busting version in: {index_html}")

    return True


def _load_updates(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("updates JSON must be a list of objects")
    return data


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: site_update.py <site_dir> <updates.json>", file=sys.stderr)
        return 2

    site_dir = Path(args[0])
    updates = _load_updates(Path(args[1]))
    changed = apply_site_updates(site_dir, updates)
    print("[summary] Download page changed" if changed else "[summary] Download page unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
