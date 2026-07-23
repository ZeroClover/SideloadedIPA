#!/usr/bin/env python3
"""Cloudflare R2 storage helpers (S3-compatible API, via boto3).

The bucket holds every publishable artifact of the signing pipeline:

    apps/<slug>/<version>/<App>.ipa   # versioned IPA (immutable, one per release)
    apps/<slug>/icon-<sha12>.png      # card icon (immutable, keyed by content)
                                      # (see ICON_CACHE_CONTROL: also no-transform)
    site/apps.json                    # the single data source for page + plist

Credentials and bucket settings come from environment variables (GitHub
secrets/vars in CI) — never from the TOML config, which stays free of
environment-specific values:

    R2_ACCOUNT_ID         Cloudflare account id (endpoint host)
    R2_ACCESS_KEY_ID      API token access key (Object Read & Write, this bucket)
    R2_SECRET_ACCESS_KEY  API token secret
    R2_BUCKET             bucket name
    R2_PUBLIC_BASE_URL    public base URL of the bucket's custom domain
                          (e.g. "https://ipa.zeroclover.io"), no trailing slash
    R2_REGION             optional signing region: the bucket's location hint
                          (wnam/enam/weur/eeur/apac/oc) or "auto" (default).

The region is always set EXPLICITLY on the client. boto3 otherwise falls back
to the ambient AWS config (~/.aws/config, AWS_DEFAULT_REGION), whose region
names (e.g. "ap-northeast-1") R2 rejects with InvalidRegionName — R2 only
accepts its own location hints plus "auto".
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from sideloadedipa.errors import ConfigurationError, ErrorCode

# Headers for versioned IPA objects: keys are never reused across releases, so
# they can be cached forever. Content-Type follows Apple's OTA deployment guide.
IPA_CONTENT_TYPE = "application/octet-stream"
IPA_CONTENT_DISPOSITION = "attachment"
IPA_CACHE_CONTROL = "public, max-age=31536000, immutable"

# apps.json is the page/plist data source; keep it short-cached at the edge.
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
JSON_CACHE_CONTROL = "public, max-age=60"

# Icons are content-addressed (apps/<slug>/icon-<sha12>.png), so a refreshed
# icon lands on a NEW key and is visible immediately — no purge needed, which
# matters because the zone overrides short max-ages with a 4-hour browser TTL
# and the pipeline holds no Cloudflare API token. Keys are never reused, so the
# same immutable caching as the versioned IPAs applies.
#
# 'no-transform' additionally opts these objects out of Cloudflare Polish, which
# is enabled zone-wide and was lossily re-encoding them at quality 85 (a 1024px
# master would reach the page visibly softened, and app icons are mostly flat
# colour and hard edges — exactly what lossy re-encoding handles worst). It also
# forgoes gzip/brotli at the edge, which costs nothing here: PNG is already
# deflate-compressed, so transfer encoding saves no meaningful bytes.
ICON_CONTENT_TYPE = "image/png"
ICON_CACHE_CONTROL = f"{IPA_CACHE_CONTROL}, no-transform"

# Length of the hex sha256 prefix in an icon key. 12 hex chars = 48 bits, far
# beyond collision range for a handful of icons per app.
ICON_DIGEST_LENGTH = 12

REQUIRED_ENV_VARS = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_PUBLIC_BASE_URL",
)

# R2 signing region per Cloudflare's docs (region_name="auto"); may be pinned
# to the bucket's location hint via the R2_REGION env var (e.g. "apac").
DEFAULT_REGION = "auto"


class R2Store:
    """Thin wrapper around a boto3 S3 client pointed at a Cloudflare R2 bucket."""

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        public_base_url: str,
        key_prefix: str = "apps",
        apps_json_key: str = "site/apps.json",
        region: str = DEFAULT_REGION,
        client: Any = None,
    ) -> None:
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")
        self.key_prefix = key_prefix.strip("/") or "apps"
        self.apps_json_key = apps_json_key
        self._client = client or boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )

    @classmethod
    def from_env(
        cls,
        key_prefix: str = "apps",
        apps_json_key: str = "site/apps.json",
        environment: Mapping[str, str] | None = None,
    ) -> "R2Store":
        """Build a store from the R2_* environment variables (R2_REGION optional)."""
        values = os.environ if environment is None else environment
        missing = [name for name in REQUIRED_ENV_VARS if not values.get(name)]
        if missing:
            raise ConfigurationError(
                ErrorCode.CONFIG_MISSING,
                "missing required R2 credentials",
                remediation="provide the complete R2 publication credential set",
                safe_details=(("variables", tuple(missing)),),
            )
        return cls(
            account_id=values["R2_ACCOUNT_ID"],
            access_key_id=values["R2_ACCESS_KEY_ID"],
            secret_access_key=values["R2_SECRET_ACCESS_KEY"],
            bucket=values["R2_BUCKET"],
            public_base_url=values["R2_PUBLIC_BASE_URL"],
            key_prefix=key_prefix,
            apps_json_key=apps_json_key,
            region=values.get("R2_REGION", DEFAULT_REGION),
        )

    # ── key / URL helpers ────────────────────────────────────────────────

    def ipa_key(self, slug: str, version: str, filename: str) -> str:
        """Versioned object key for a signed IPA: ``apps/<slug>/<version>/<file>``."""
        return f"{self.key_prefix}/{slug}/{version}/{filename}"

    def icon_key(self, slug: str, png_bytes: bytes) -> str:
        """Content-addressed icon key: ``apps/<slug>/icon-<sha12>.png``.

        Derived from the bytes rather than the slug, so re-uploading an
        unchanged icon is a no-op and a changed one gets a fresh, uncached URL.
        """
        digest = hashlib.sha256(png_bytes).hexdigest()[:ICON_DIGEST_LENGTH]
        return f"{self.key_prefix}/{slug}/icon-{digest}.png"

    def public_url(self, key: str) -> str:
        return f"{self.public_base_url}/{key}"

    def key_from_url(self, url: str) -> Optional[str]:
        """Map a public URL back to its object key; ``None`` if not on this bucket."""
        prefix = f"{self.public_base_url}/"
        if url.startswith(prefix):
            return url[len(prefix) :]
        return None

    # ── uploads ──────────────────────────────────────────────────────────

    def upload_file(
        self,
        local_path: Path,
        key: str,
        content_type: str,
        cache_control: str,
        content_disposition: Optional[str] = None,
    ) -> str:
        """Upload a file with explicit headers; returns its public URL."""
        extra: dict[str, str] = {"ContentType": content_type, "CacheControl": cache_control}
        if content_disposition:
            extra["ContentDisposition"] = content_disposition
        self._client.upload_file(str(local_path), self.bucket, key, ExtraArgs=extra)
        url = self.public_url(key)
        print(f"[info] Uploaded: {url}")
        return url

    def upload_ipa(self, local_path: Path, key: str) -> str:
        """Upload a signed IPA with immutable-cache headers; returns its public URL."""
        return self.upload_file(
            local_path,
            key,
            content_type=IPA_CONTENT_TYPE,
            cache_control=IPA_CACHE_CONTROL,
            content_disposition=IPA_CONTENT_DISPOSITION,
        )

    def upload_icon(self, slug: str, png_bytes: bytes) -> str:
        """Upload a normalised PNG icon to its content-addressed key; returns its URL."""
        key = self.icon_key(slug, png_bytes)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=png_bytes,
            ContentType=ICON_CONTENT_TYPE,
            CacheControl=ICON_CACHE_CONTROL,
        )
        url = self.public_url(key)
        print(f"[info] Uploaded icon: {url} ({len(png_bytes)} bytes)")
        return url

    def upload_json(self, key: str, payload: dict[str, Any]) -> str:
        """Upload a JSON document (e.g. apps.json); returns its public URL."""
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=JSON_CONTENT_TYPE,
            CacheControl=JSON_CACHE_CONTROL,
        )
        url = self.public_url(key)
        print(f"[info] Uploaded JSON: {url}")
        return url

    # ── downloads ────────────────────────────────────────────────────────

    def download_bytes(self, key: str) -> bytes:
        """Fetch an object's raw bytes; raises ``ClientError`` when it is missing."""
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def download_json(self, key: str) -> Optional[dict[str, Any]]:
        """Fetch and parse a JSON object; ``None`` when the key does not exist."""
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "NoSuchBucket", "404"):
                return None
            raise
        data: dict[str, Any] = json.loads(response["Body"].read().decode("utf-8"))
        return data

    # ── stale-version cleanup (D7) ───────────────────────────────────────

    def cleanup_stale(self, slugs: list[str], referenced_keys: set[str]) -> list[str]:
        """Delete objects under ``apps/<slug>/`` that apps.json no longer references.

        Covers superseded IPA versions and superseded content-addressed icons
        alike. The whitelist is derived from the *current* apps.json reference
        set (not from time), so a key is only ever deleted once nothing points
        at it — an app whose icon refresh was skipped keeps the icon its entry
        still names.
        Only the given slugs (the ones this run rebuilt) are inspected — manual
        app entries are never touched. Returns the list of deleted keys.
        """
        deleted: list[str] = []
        for slug in slugs:
            prefix = f"{self.key_prefix}/{slug}/"
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])

            stale = [key for key in keys if key not in referenced_keys]
            if not stale:
                continue
            self.delete_keys(stale)
            deleted.extend(stale)
        return deleted

    def delete_keys(self, keys: list[str]) -> None:
        """Delete the given object keys in one batch."""
        if not keys:
            return
        # S3 delete_objects accepts up to 1000 keys per call; our volumes are tiny.
        self._client.delete_objects(
            Bucket=self.bucket,
            Delete={"Objects": [{"Key": key} for key in keys]},
        )
        for key in keys:
            print(f"[info] Deleted object: {key}")


def referenced_keys_from_apps(store: R2Store, apps: list[dict[str, Any]]) -> set[str]:
    """Collect the object keys referenced by apps.json entries (ipaUrl + iconUrl)."""
    keys: set[str] = set()
    for app in apps:
        for field in ("ipaUrl", "iconUrl"):
            url = app.get(field) or ""
            key = store.key_from_url(url)
            if key:
                keys.add(key)
    return keys


def main() -> int:  # pragma: no cover - manual smoke helper
    store = R2Store.from_env()
    doc = store.download_json(store.apps_json_key)
    print(json.dumps(doc, indent=2, ensure_ascii=False) if doc else "(apps.json missing)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
