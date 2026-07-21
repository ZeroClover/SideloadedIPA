"""Tests for scripts/migrate_icon_keys.py - one-time icon re-keying migration."""

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from migrate_icon_keys import migrate
from r2_store import R2Store

BASE_URL = "https://ipa.zeroclover.io"
PNG = b"\x89PNG\r\n\x1a\nehpanda"
DIGEST = hashlib.sha256(PNG).hexdigest()[:12]


def _store(doc: Optional[Dict[str, Any]], client: Optional[MagicMock] = None) -> R2Store:
    client = client or MagicMock()
    store = R2Store(
        account_id="acc123",
        access_key_id="akid",
        secret_access_key="secret",
        bucket="zeroclover-ipa",
        public_base_url=BASE_URL,
        client=client,
    )
    store.download_json = MagicMock(return_value=doc)  # type: ignore[method-assign]
    store.download_bytes = MagicMock(return_value=PNG)  # type: ignore[method-assign]
    store.upload_json = MagicMock()  # type: ignore[method-assign]
    return store


def _doc(apps: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"updatedAt": "2026-07-18T04:00:00Z", "apps": apps}


def _legacy_app() -> Dict[str, Any]:
    return {
        "slug": "ehpanda",
        "name": "EhPanda",
        "version": "2.7.4",
        "ipaUrl": f"{BASE_URL}/apps/ehpanda/2.7.4/EhPanda.ipa",
        "iconUrl": f"{BASE_URL}/apps/ehpanda/icon.png",
    }


class TestDryRun:
    """Without --apply nothing is written."""

    def test_writes_nothing(self) -> None:
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        assert migrate(store, apply=False, delete_legacy=False) == 0

        store.upload_json.assert_not_called()
        client.put_object.assert_not_called()
        client.delete_objects.assert_not_called()


class TestApply:
    """The migration re-uploads under the hashed key and rewrites apps.json."""

    def test_rekeys_legacy_icon(self) -> None:
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        assert migrate(store, apply=True, delete_legacy=False) == 0

        # Re-uploaded under the content-addressed key...
        assert client.put_object.call_args.kwargs["Key"] == f"apps/ehpanda/icon-{DIGEST}.png"
        # ...and the registry now points at it.
        uploaded_doc = store.upload_json.call_args[0][1]
        assert uploaded_doc["apps"][0]["iconUrl"] == f"{BASE_URL}/apps/ehpanda/icon-{DIGEST}.png"
        assert uploaded_doc["updatedAt"] != "2026-07-18T04:00:00Z"

    def test_legacy_object_kept_by_default(self) -> None:
        """cleanup_stale retires it on the next run; the migration doesn't delete."""
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        migrate(store, apply=True, delete_legacy=False)

        client.delete_objects.assert_not_called()

    def test_delete_legacy_removes_old_key(self) -> None:
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        migrate(store, apply=True, delete_legacy=True)

        client.delete_objects.assert_called_once_with(
            Bucket="zeroclover-ipa",
            Delete={"Objects": [{"Key": "apps/ehpanda/icon.png"}]},
        )

    def test_deletes_only_after_apps_json_upload(self) -> None:
        """Order matters: a delete before the rewrite would 404 live cards."""
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)
        calls: List[str] = []
        store.upload_json.side_effect = lambda *a, **k: calls.append("upload_json")
        client.delete_objects.side_effect = lambda *a, **k: calls.append("delete_objects")

        migrate(store, apply=True, delete_legacy=True)

        assert calls == ["upload_json", "delete_objects"]


class TestSkips:
    """Entries that are not legacy-keyed are left alone."""

    def test_already_hashed_entry_untouched(self) -> None:
        client = MagicMock()
        app = dict(_legacy_app(), iconUrl=f"{BASE_URL}/apps/ehpanda/icon-abc123abc123.png")
        store = _store(_doc([app]), client)

        assert migrate(store, apply=True, delete_legacy=False) == 0

        store.upload_json.assert_not_called()
        client.put_object.assert_not_called()

    def test_iconless_entry_untouched(self) -> None:
        store = _store(_doc([dict(_legacy_app(), iconUrl="")]))

        assert migrate(store, apply=True, delete_legacy=False) == 0

        store.upload_json.assert_not_called()

    def test_foreign_host_icon_untouched(self) -> None:
        """A manual app hosted elsewhere maps to no key on this bucket."""
        app = dict(_legacy_app(), iconUrl="https://itms.zeroclover.io/ehpanda/icon.png")
        store = _store(_doc([app]))

        assert migrate(store, apply=True, delete_legacy=False) == 0

        store.upload_json.assert_not_called()

    def test_missing_apps_json_fails(self) -> None:
        store = _store(None)
        assert migrate(store, apply=True, delete_legacy=False) == 1

    def test_unreadable_icon_aborts_without_writing(self) -> None:
        """Abort whole-hog rather than leave apps.json half-migrated."""
        store = _store(_doc([_legacy_app()]))
        store.download_bytes.side_effect = RuntimeError("NoSuchKey")  # type: ignore[attr-defined]

        assert migrate(store, apply=True, delete_legacy=False) == 1

        store.upload_json.assert_not_called()
