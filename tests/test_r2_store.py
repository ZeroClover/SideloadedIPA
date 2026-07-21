"""Tests for scripts/r2_store.py - Cloudflare R2 (S3-compatible) storage wrapper."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from r2_store import (
    ICON_CACHE_CONTROL,
    ICON_CONTENT_TYPE,
    IPA_CACHE_CONTROL,
    IPA_CONTENT_DISPOSITION,
    IPA_CONTENT_TYPE,
    R2Store,
    referenced_keys_from_apps,
)

BASE_URL = "https://ipa.zeroclover.io"


def _store(client: MagicMock | None = None) -> R2Store:
    return R2Store(
        account_id="acc123",
        access_key_id="akid",
        secret_access_key="secret",
        bucket="zeroclover-ipa",
        public_base_url=BASE_URL,
        client=client or MagicMock(),
    )


class TestFromEnv:
    """R2Store.from_env requires the full R2_* environment."""

    def test_missing_vars_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "R2_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET",
            "R2_PUBLIC_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
            R2Store.from_env()

    def test_builds_client_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("R2_ACCOUNT_ID", "acc123")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "akid")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
        monkeypatch.setenv("R2_BUCKET", "bucket")
        monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://ipa.example.com/")
        monkeypatch.delenv("R2_REGION", raising=False)
        with patch("r2_store.boto3.client") as mock_client:
            store = R2Store.from_env()
        assert store.bucket == "bucket"
        # trailing slash stripped
        assert store.public_base_url == "https://ipa.example.com"
        mock_client.assert_called_once_with(
            "s3",
            endpoint_url="https://acc123.r2.cloudflarestorage.com",
            aws_access_key_id="akid",
            aws_secret_access_key="secret",
            # region is always explicit: ambient AWS config (e.g. ap-northeast-1)
            # is rejected by R2 with InvalidRegionName
            region_name="auto",
        )

    def test_region_pinned_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """R2_REGION pins the signing region to the bucket's location hint."""
        monkeypatch.setenv("R2_ACCOUNT_ID", "acc123")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "akid")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
        monkeypatch.setenv("R2_BUCKET", "bucket")
        monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://ipa.example.com")
        monkeypatch.setenv("R2_REGION", "apac")
        with patch("r2_store.boto3.client") as mock_client:
            R2Store.from_env()
        assert mock_client.call_args.kwargs["region_name"] == "apac"


class TestKeyHelpers:
    """Object key / public URL mapping."""

    def test_ipa_key_is_versioned(self) -> None:
        store = _store()
        assert store.ipa_key("ehpanda", "2.7.4", "EhPanda.ipa") == (
            "apps/ehpanda/2.7.4/EhPanda.ipa"
        )

    def test_icon_key(self) -> None:
        store = _store()
        assert store.icon_key("ehpanda") == "apps/ehpanda/icon.png"

    def test_public_url(self) -> None:
        store = _store()
        assert store.public_url("apps/ehpanda/icon.png") == (f"{BASE_URL}/apps/ehpanda/icon.png")

    def test_key_from_url_roundtrip(self) -> None:
        store = _store()
        url = f"{BASE_URL}/apps/ehpanda/2.7.4/EhPanda.ipa"
        assert store.key_from_url(url) == "apps/ehpanda/2.7.4/EhPanda.ipa"

    def test_key_from_url_foreign_host(self) -> None:
        store = _store()
        assert store.key_from_url("https://itms.zeroclover.io/ehpanda/EhPanda.ipa") is None


class TestUploadIpa:
    """IPA uploads must carry Apple's content type and immutable cache headers."""

    def test_upload_args(self, tmp_path: Path) -> None:
        client = MagicMock()
        store = _store(client)
        ipa = tmp_path / "EhPanda.ipa"
        ipa.write_bytes(b"fake-ipa")

        url = store.upload_ipa(ipa, "apps/ehpanda/2.7.4/EhPanda.ipa")

        assert url == f"{BASE_URL}/apps/ehpanda/2.7.4/EhPanda.ipa"
        client.upload_file.assert_called_once_with(
            str(ipa),
            "zeroclover-ipa",
            "apps/ehpanda/2.7.4/EhPanda.ipa",
            ExtraArgs={
                "ContentType": IPA_CONTENT_TYPE,
                "ContentDisposition": IPA_CONTENT_DISPOSITION,
                "CacheControl": IPA_CACHE_CONTROL,
            },
        )


class TestUploadAndDownloadJson:
    """apps.json document round-trip."""

    def test_upload_json_serialises_document(self) -> None:
        client = MagicMock()
        store = _store(client)
        doc = {"updatedAt": "2026-07-18T04:00:00Z", "apps": [{"slug": "ehpanda"}]}

        store.upload_json("site/apps.json", doc)

        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "zeroclover-ipa"
        assert kwargs["Key"] == "site/apps.json"
        assert json.loads(kwargs["Body"].decode("utf-8")) == doc

    def test_download_json_parses_body(self) -> None:
        client = MagicMock()
        payload = {"updatedAt": None, "apps": []}
        client.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(payload).encode())
        }
        store = _store(client)
        assert store.download_json("site/apps.json") == payload

    def test_download_json_missing_key_returns_none(self) -> None:
        client = MagicMock()
        client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject"
        )
        store = _store(client)
        assert store.download_json("site/apps.json") is None

    def test_download_json_other_errors_raise(self) -> None:
        client = MagicMock()
        client.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
        )
        store = _store(client)
        with pytest.raises(ClientError):
            store.download_json("site/apps.json")


class TestCleanupStale:
    """Stale-version cleanup deletes only unreferenced keys of the given slugs."""

    def _paginated(self, client: MagicMock, pages: list[dict]) -> None:
        paginator = MagicMock()
        paginator.paginate.return_value = pages
        client.get_paginator.return_value = paginator

    def test_deletes_only_unreferenced_keys(self) -> None:
        client = MagicMock()
        self._paginated(
            client,
            [
                {
                    "Contents": [
                        {"Key": "apps/ehpanda/2.7.4/EhPanda.ipa"},
                        {"Key": "apps/ehpanda/2.7.3/EhPanda.ipa"},
                        {"Key": "apps/ehpanda/icon.png"},
                    ]
                }
            ],
        )
        store = _store(client)
        referenced = {"apps/ehpanda/2.7.4/EhPanda.ipa", "apps/ehpanda/icon.png"}

        deleted = store.cleanup_stale(["ehpanda"], referenced)

        assert deleted == ["apps/ehpanda/2.7.3/EhPanda.ipa"]
        client.delete_objects.assert_called_once_with(
            Bucket="zeroclover-ipa",
            Delete={"Objects": [{"Key": "apps/ehpanda/2.7.3/EhPanda.ipa"}]},
        )

    def test_nothing_stale_skips_delete(self) -> None:
        client = MagicMock()
        self._paginated(client, [{"Contents": [{"Key": "apps/ehpanda/icon.png"}]}])
        store = _store(client)

        deleted = store.cleanup_stale(["ehpanda"], {"apps/ehpanda/icon.png"})

        assert deleted == []
        client.delete_objects.assert_not_called()

    def test_only_requested_slugs_scanned(self) -> None:
        """Manual apps are never touched: only the given slugs get listed."""
        client = MagicMock()
        self._paginated(client, [{"Contents": []}])
        store = _store(client)

        store.cleanup_stale(["JHenTai"], set())

        paginate_kwargs = client.get_paginator.return_value.paginate.call_args.kwargs
        assert paginate_kwargs["Prefix"] == "apps/JHenTai/"


class TestReferencedKeysFromApps:
    """Whitelist derivation from apps.json entries."""

    def test_collects_ipa_and_icon_keys(self) -> None:
        store = _store()
        apps = [
            {
                "slug": "ehpanda",
                "ipaUrl": f"{BASE_URL}/apps/ehpanda/2.7.4/EhPanda.ipa",
                "iconUrl": f"{BASE_URL}/apps/ehpanda/icon.png",
            },
            {
                # manual app still hosted elsewhere: contributes no keys
                "slug": "legacy",
                "ipaUrl": "https://itms.zeroclover.io/legacy/legacy.ipa",
                "iconUrl": "",
            },
        ]
        keys = referenced_keys_from_apps(store, apps)
        assert keys == {
            "apps/ehpanda/2.7.4/EhPanda.ipa",
            "apps/ehpanda/icon.png",
        }


class TestUploadIcon:
    """Icons sit at a stable, mutable key and must not be cached immutably."""

    def test_uploads_png_with_icon_headers(self) -> None:
        client = MagicMock()
        store = _store(client)
        url = store.upload_icon("JHenTai", b"\x89PNG\r\n\x1a\nfake")

        assert url == f"{BASE_URL}/apps/JHenTai/icon.png"
        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Key"] == "apps/JHenTai/icon.png"
        assert kwargs["Body"] == b"\x89PNG\r\n\x1a\nfake"
        assert kwargs["ContentType"] == ICON_CONTENT_TYPE
        assert kwargs["CacheControl"] == ICON_CACHE_CONTROL
        assert "immutable" not in kwargs["CacheControl"]
