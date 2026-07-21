"""Tests for scripts/apps_registry.py - R2 apps.json registry merging."""

import datetime
import sys
from pathlib import Path

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from apps_registry import merge_apps, merge_registry_doc

SAMPLE_APPS = [
    {
        "slug": "ehpanda",
        "name": "EhPanda",
        "bundleId": "io.zeroclover.app.ehpanda",
        "version": "2.7.4",
        "ipaUrl": "https://ipa.zeroclover.io/apps/ehpanda/2.7.4/EhPanda.ipa",
        "iconUrl": "https://ipa.zeroclover.io/apps/ehpanda/icon.png",
    },
    {
        "slug": "JHenTai",
        "name": "JHenTai",
        "bundleId": "io.zeroclover.app.jhentai",
        "version": "7.4.10",
        "ipaUrl": "https://ipa.zeroclover.io/apps/JHenTai/7.4.10/JHenTai.ipa",
        "iconUrl": "https://ipa.zeroclover.io/apps/JHenTai/icon.png",
    },
]


def _update(slug: str, **fields) -> dict:
    update = {
        "slug": slug,
        "name": slug,
        "bundleId": "io.zeroclover.app.x",
        "version": "0.0.1",
        "ipaUrl": "https://ipa.zeroclover.io/apps/x/0.0.1/x.ipa",
        "iconUrl": "https://ipa.zeroclover.io/apps/x/icon.png",
    }
    update.update(fields)
    return update


class TestMergeApps:
    """Tests for merging signing results into the registry entry list."""

    def test_update_existing_version(self) -> None:
        updates = [
            _update(
                "JHenTai",
                version="7.5.0",
                ipaUrl="https://ipa.zeroclover.io/apps/JHenTai/7.5.0/JHenTai.ipa",
            )
        ]
        merged, changed = merge_apps(SAMPLE_APPS, updates)
        assert changed is True
        jhentai = next(a for a in merged if a["slug"] == "JHenTai")
        assert jhentai["version"] == "7.5.0"
        assert jhentai["ipaUrl"].endswith("/7.5.0/JHenTai.ipa")

    def test_update_bundle_id(self) -> None:
        updates = [_update("JHenTai", bundleId="io.zeroclover.app.jhentai.new")]
        merged, changed = merge_apps(SAMPLE_APPS, updates)
        assert changed is True
        jhentai = next(a for a in merged if a["slug"] == "JHenTai")
        assert jhentai["bundleId"] == "io.zeroclover.app.jhentai.new"

    def test_empty_icon_url_preserves_existing(self) -> None:
        """A task with no icon_path reports iconUrl="" and must not clear the entry.

        Load-bearing since icon keys became content-addressed: there is no
        conventional URL to reconstruct, so the old hashed one is all there is.
        """
        existing = [dict(SAMPLE_APPS[0], iconUrl="https://ipa.zeroclover.io/a/icon-abc123.png")]
        merged, _ = merge_apps(existing, [_update("ehpanda", version="2.8.0", iconUrl="")])
        assert merged[0]["iconUrl"] == "https://ipa.zeroclover.io/a/icon-abc123.png"
        assert merged[0]["version"] == "2.8.0"

    def test_new_entry_tolerates_empty_icon_url(self) -> None:
        """A brand-new app whose icon fetch failed is still published, icon-less."""
        merged, changed = merge_apps([], [_update("newapp", iconUrl="")])
        assert changed is True
        assert merged[0]["iconUrl"] == ""

    def test_refreshes_name_from_app_name(self) -> None:
        """Existing entries adopt the task's app_name (no curated-name logic)."""
        updates = [_update("ehpanda", name="EhPanda Reloaded", version="2.8.0")]
        merged, changed = merge_apps(SAMPLE_APPS, updates)
        assert changed is True
        entry = next(a for a in merged if a["slug"] == "ehpanda")
        assert entry["name"] == "EhPanda Reloaded"
        assert entry["version"] == "2.8.0"

    def test_add_new_app_appended(self) -> None:
        updates = [_update("PiliPlus", name="PiliPlus")]
        merged, changed = merge_apps(SAMPLE_APPS, updates)
        assert changed is True
        assert merged[-1]["slug"] == "PiliPlus"
        assert merged[-1]["name"] == "PiliPlus"
        assert len(merged) == 3

    def test_no_change_when_identical(self) -> None:
        jhentai = next(a for a in SAMPLE_APPS if a["slug"] == "JHenTai")
        merged, changed = merge_apps(SAMPLE_APPS, [dict(jhentai)])
        assert changed is False
        assert merged == SAMPLE_APPS

    def test_unmentioned_entries_untouched(self) -> None:
        """Entries the pipeline does not mention stay exactly as they were."""
        updates = [_update("JHenTai", version="9.9.9")]
        merged, changed = merge_apps(SAMPLE_APPS, updates)
        assert changed is True
        ehpanda = next(a for a in merged if a["slug"] == "ehpanda")
        assert ehpanda == SAMPLE_APPS[0]


class TestMergeRegistryDoc:
    """Tests for full-document merging (updatedAt stamping, bootstrap)."""

    NOW = datetime.datetime(2026, 7, 18, 4, 0, 0, tzinfo=datetime.timezone.utc)

    def test_bootstrap_from_missing_doc(self) -> None:
        """apps.json missing on R2 (first run) bootstraps an empty registry."""
        doc, changed = merge_registry_doc(None, [_update("JHenTai", name="JHenTai")], now=self.NOW)
        assert changed is True
        assert doc["updatedAt"] == "2026-07-18T04:00:00Z"
        assert [a["slug"] for a in doc["apps"]] == ["JHenTai"]

    def test_change_stamps_updated_at(self) -> None:
        existing = {"updatedAt": "2026-07-01T00:00:00Z", "apps": SAMPLE_APPS}
        doc, changed = merge_registry_doc(existing, [_update("JHenTai", version="7.5.0")], now=self.NOW)
        assert changed is True
        assert doc["updatedAt"] == "2026-07-18T04:00:00Z"

    def test_no_change_preserves_doc(self) -> None:
        jhentai = next(a for a in SAMPLE_APPS if a["slug"] == "JHenTai")
        existing = {"updatedAt": "2026-07-01T00:00:00Z", "apps": SAMPLE_APPS}
        doc, changed = merge_registry_doc(existing, [dict(jhentai)], now=self.NOW)
        assert changed is False
        assert doc["updatedAt"] == "2026-07-01T00:00:00Z"
        assert doc["apps"] == SAMPLE_APPS
