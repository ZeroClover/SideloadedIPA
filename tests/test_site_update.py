"""Tests for scripts/site_update.py - download page data refresh."""

import sys
from pathlib import Path

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from site_update import (
    apply_site_updates,
    bump_index_asset_version,
    merge_apps,
    parse_apps_js,
    render_apps_array,
    update_apps_js_content,
)

SAMPLE_APPS_JS = """/*
 * header comment
 */
window.ZC_APPS = [
  { name: 'EhPanda',   dir: 'ehpanda',   bundleId: 'io.zeroclover.app.ehpanda',   version: '2.7.4'  },
  { name: 'JHenTai',   dir: 'JHenTai',   bundleId: 'io.zeroclover.app.jhentai',   version: '7.4.10' },
];
"""

SAMPLE_INDEX_HTML = """<!DOCTYPE html>
<link rel="stylesheet" href="styles.css?v=2">
<script src="apps.js?v=2"></script>
<script src="app-card.js?v=2"></script>
<script src="app.js?v=2"></script>
"""


class TestParseAppsJs:
    """Tests for parsing the ZC_APPS array literal."""

    def test_parses_all_entries(self) -> None:
        apps = parse_apps_js(SAMPLE_APPS_JS)
        assert len(apps) == 2
        assert apps[0] == {
            "name": "EhPanda",
            "dir": "ehpanda",
            "bundleId": "io.zeroclover.app.ehpanda",
            "version": "2.7.4",
        }

    def test_missing_array_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="ZC_APPS"):
            parse_apps_js("const x = 1;")


class TestMergeApps:
    """Tests for merging signing results into the app list."""

    def test_update_existing_version(self) -> None:
        existing = parse_apps_js(SAMPLE_APPS_JS)
        updates = [
            {
                "name": "JHenTai",
                "dir": "JHenTai",
                "bundleId": "io.zeroclover.app.jhentai",
                "version": "7.5.0",
            }
        ]
        merged, changed = merge_apps(existing, updates)
        assert changed is True
        jhentai = next(a for a in merged if a["dir"] == "JHenTai")
        assert jhentai["version"] == "7.5.0"

    def test_update_bundle_id(self) -> None:
        existing = parse_apps_js(SAMPLE_APPS_JS)
        updates = [
            {
                "name": "JHenTai",
                "dir": "JHenTai",
                "bundleId": "io.zeroclover.app.jhentai.new",
                "version": "7.4.10",
            }
        ]
        merged, changed = merge_apps(existing, updates)
        assert changed is True
        jhentai = next(a for a in merged if a["dir"] == "JHenTai")
        assert jhentai["bundleId"] == "io.zeroclover.app.jhentai.new"

    def test_preserves_curated_name(self) -> None:
        """Existing entries keep their display name even if the update differs."""
        existing = parse_apps_js(SAMPLE_APPS_JS)
        updates = [
            {
                "name": "Eros FE",  # renamed upstream
                "dir": "ehpanda",
                "bundleId": "io.zeroclover.app.ehpanda",
                "version": "2.8.0",
            }
        ]
        merged, changed = merge_apps(existing, updates)
        assert changed is True
        entry = next(a for a in merged if a["dir"] == "ehpanda")
        assert entry["name"] == "EhPanda"  # name preserved
        assert entry["version"] == "2.8.0"  # version refreshed

    def test_add_new_app_appended(self) -> None:
        existing = parse_apps_js(SAMPLE_APPS_JS)
        updates = [
            {
                "name": "PiliPlus",
                "dir": "PiliPlus",
                "bundleId": "io.zeroclover.app.piliplus",
                "version": "1.0.0",
            }
        ]
        merged, changed = merge_apps(existing, updates)
        assert changed is True
        assert merged[-1]["dir"] == "PiliPlus"
        assert len(merged) == 3

    def test_no_change_when_identical(self) -> None:
        existing = parse_apps_js(SAMPLE_APPS_JS)
        updates = [
            {
                "name": "JHenTai",
                "dir": "JHenTai",
                "bundleId": "io.zeroclover.app.jhentai",
                "version": "7.4.10",
            }
        ]
        merged, changed = merge_apps(existing, updates)
        assert changed is False


class TestRenderRoundtrip:
    """Render output should be parseable back to the same data."""

    def test_roundtrip_preserves_data(self) -> None:
        apps = parse_apps_js(SAMPLE_APPS_JS)
        rendered = "window.ZC_APPS = " + render_apps_array(apps) + ";"
        reparsed = parse_apps_js(rendered)
        assert reparsed == apps


class TestUpdateAppsJsContent:
    """Tests for the in-place content update."""

    def test_preserves_header(self) -> None:
        new_content, changed = update_apps_js_content(
            SAMPLE_APPS_JS,
            [
                {
                    "name": "JHenTai",
                    "dir": "JHenTai",
                    "bundleId": "io.zeroclover.app.jhentai",
                    "version": "8.0.0",
                }
            ],
        )
        assert changed is True
        assert new_content.startswith("/*\n * header comment")
        assert "8.0.0" in new_content
        # trailing structure preserved
        assert new_content.rstrip().endswith("];")

    def test_noop_returns_original(self) -> None:
        new_content, changed = update_apps_js_content(
            SAMPLE_APPS_JS,
            [
                {
                    "name": "JHenTai",
                    "dir": "JHenTai",
                    "bundleId": "io.zeroclover.app.jhentai",
                    "version": "7.4.10",
                }
            ],
        )
        assert changed is False
        assert new_content == SAMPLE_APPS_JS


class TestBumpIndexAssetVersion:
    """Tests for cache-busting query bumps."""

    def test_bumps_only_target_asset(self) -> None:
        new_html, changed = bump_index_asset_version(SAMPLE_INDEX_HTML, "apps.js")
        assert changed is True
        assert "apps.js?v=3" in new_html
        # other assets untouched
        assert "app-card.js?v=2" in new_html
        assert "app.js?v=2" in new_html
        assert "styles.css?v=2" in new_html

    def test_no_match_returns_unchanged(self) -> None:
        new_html, changed = bump_index_asset_version(SAMPLE_INDEX_HTML, "missing.js")
        assert changed is False
        assert new_html == SAMPLE_INDEX_HTML


class TestApplySiteUpdates:
    """End-to-end on-disk update."""

    def test_writes_files_when_changed(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        (site_dir / "apps.js").write_text(SAMPLE_APPS_JS)
        (site_dir / "index.html").write_text(SAMPLE_INDEX_HTML)

        changed = apply_site_updates(
            site_dir,
            [
                {
                    "name": "JHenTai",
                    "dir": "JHenTai",
                    "bundleId": "io.zeroclover.app.jhentai",
                    "version": "9.9.9",
                }
            ],
        )
        assert changed is True
        assert "9.9.9" in (site_dir / "apps.js").read_text()
        assert "apps.js?v=3" in (site_dir / "index.html").read_text()

    def test_noop_leaves_files_untouched(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        (site_dir / "apps.js").write_text(SAMPLE_APPS_JS)
        (site_dir / "index.html").write_text(SAMPLE_INDEX_HTML)

        changed = apply_site_updates(
            site_dir,
            [
                {
                    "name": "JHenTai",
                    "dir": "JHenTai",
                    "bundleId": "io.zeroclover.app.jhentai",
                    "version": "7.4.10",
                }
            ],
        )
        assert changed is False
        # index.html untouched (no version bump)
        assert "apps.js?v=2" in (site_dir / "index.html").read_text()
