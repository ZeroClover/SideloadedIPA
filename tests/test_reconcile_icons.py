"""Tests for scripts/reconcile_icons.py - icon key + header reconciliation."""

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from r2_store import ICON_CACHE_CONTROL, R2Store
from reconcile_icons import reconcile

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


def _app(icon_url: str) -> Dict[str, Any]:
    return {
        "slug": "ehpanda",
        "name": "EhPanda",
        "version": "2.7.4",
        "ipaUrl": f"{BASE_URL}/apps/ehpanda/2.7.4/EhPanda.ipa",
        "iconUrl": icon_url,
    }


def _legacy_app() -> Dict[str, Any]:
    return _app(f"{BASE_URL}/apps/ehpanda/icon.png")


def _hashed_app() -> Dict[str, Any]:
    """An entry already on its correct content-addressed key (header-only work)."""
    return _app(f"{BASE_URL}/apps/ehpanda/icon-{DIGEST}.png")


class TestDryRun:
    """Without --apply nothing is written."""

    def test_writes_nothing(self) -> None:
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        assert reconcile(store, apply=False, delete_legacy=False) == 0

        store.upload_json.assert_not_called()
        client.put_object.assert_not_called()
        client.delete_objects.assert_not_called()


class TestRekey:
    """Legacy-keyed icons move to their content-addressed key."""

    def test_rekeys_legacy_icon(self) -> None:
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        assert reconcile(store, apply=True, delete_legacy=False) == 0

        # Re-uploaded under the content-addressed key...
        assert client.put_object.call_args.kwargs["Key"] == f"apps/ehpanda/icon-{DIGEST}.png"
        # ...and the registry now points at it.
        uploaded_doc = store.upload_json.call_args[0][1]
        assert uploaded_doc["apps"][0]["iconUrl"] == f"{BASE_URL}/apps/ehpanda/icon-{DIGEST}.png"
        assert uploaded_doc["updatedAt"] != "2026-07-18T04:00:00Z"

    def test_legacy_object_kept_by_default(self) -> None:
        """cleanup_stale retires it on the next run; reconcile doesn't delete."""
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        reconcile(store, apply=True, delete_legacy=False)

        client.delete_objects.assert_not_called()

    def test_delete_legacy_removes_old_key(self) -> None:
        client = MagicMock()
        store = _store(_doc([_legacy_app()]), client)

        reconcile(store, apply=True, delete_legacy=True)

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

        reconcile(store, apply=True, delete_legacy=True)

        assert calls == ["upload_json", "delete_objects"]


class TestHeaderRefresh:
    """An already-correct key still gets re-uploaded, to restamp its headers."""

    def test_reuploads_in_place_with_current_headers(self) -> None:
        client = MagicMock()
        store = _store(_doc([_hashed_app()]), client)

        assert reconcile(store, apply=True, delete_legacy=False) == 0

        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Key"] == f"apps/ehpanda/icon-{DIGEST}.png"
        assert kwargs["CacheControl"] == ICON_CACHE_CONTROL
        assert "no-transform" in kwargs["CacheControl"]

    def test_apps_json_untouched_when_no_key_moved(self) -> None:
        """A header-only pass changes no URL, so the registry must not be rewritten."""
        store = _store(_doc([_hashed_app()]), MagicMock())

        reconcile(store, apply=True, delete_legacy=False)

        store.upload_json.assert_not_called()

    def test_delete_legacy_deletes_nothing_when_no_legacy_key(self) -> None:
        """--delete-legacy on an already-migrated bucket must not delete the live icon."""
        client = MagicMock()
        store = _store(_doc([_hashed_app()]), client)

        reconcile(store, apply=True, delete_legacy=True)

        client.delete_objects.assert_not_called()

    def test_idempotent_second_pass(self) -> None:
        """Re-running after a successful pass repeats the upload and nothing else."""
        client = MagicMock()
        store = _store(_doc([_hashed_app()]), client)

        assert reconcile(store, apply=True, delete_legacy=False) == 0
        assert reconcile(store, apply=True, delete_legacy=False) == 0

        assert client.put_object.call_count == 2
        store.upload_json.assert_not_called()


class TestSkips:
    """Entries with nothing actionable are left alone."""

    def test_iconless_entry_untouched(self) -> None:
        client = MagicMock()
        store = _store(_doc([_app("")]), client)

        assert reconcile(store, apply=True, delete_legacy=False) == 0

        store.upload_json.assert_not_called()
        client.put_object.assert_not_called()

    def test_foreign_host_icon_untouched(self) -> None:
        """A manual app hosted elsewhere maps to no key on this bucket."""
        client = MagicMock()
        store = _store(_doc([_app("https://itms.zeroclover.io/ehpanda/icon.png")]), client)

        assert reconcile(store, apply=True, delete_legacy=False) == 0

        store.upload_json.assert_not_called()
        client.put_object.assert_not_called()

    def test_missing_apps_json_fails(self) -> None:
        store = _store(None)
        assert reconcile(store, apply=True, delete_legacy=False) == 1

    def test_unreadable_icon_aborts_without_writing(self) -> None:
        """Abort whole-hog rather than leave apps.json half-reconciled."""
        store = _store(_doc([_legacy_app()]))
        store.download_bytes.side_effect = RuntimeError("NoSuchKey")  # type: ignore[attr-defined]

        assert reconcile(store, apply=True, delete_legacy=False) == 1

        store.upload_json.assert_not_called()
