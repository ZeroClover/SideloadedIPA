#!/usr/bin/env python3
"""Cloudflare R2 storage helpers (S3-compatible API, via boto3).

The bucket holds every publishable artifact of the signing pipeline:

    apps/<slug>/<version>/<App>.ipa   # versioned IPA (immutable, one per release)
    apps/<slug>/icon.png              # card icon (migrated once from the old server)
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

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

# Headers for versioned IPA objects: keys are never reused across releases, so
# they can be cached forever. Content-Type follows Apple's OTA deployment guide.
IPA_CONTENT_TYPE = "application/octet-stream"
IPA_CONTENT_DISPOSITION = "attachment"
IPA_CACHE_CONTROL = "public, max-age=31536000, immutable"

# apps.json is the page/plist data source; keep it short-cached at the edge.
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
JSON_CACHE_CONTROL = "public, max-age=60"

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
    def from_env(cls, key_prefix: str = "apps", apps_json_key: str = "site/apps.json") -> "R2Store":
        """Build a store from the R2_* environment variables (R2_REGION optional)."""
        missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
        if missing:
            raise RuntimeError("Missing required environment variable(s): " + ", ".join(missing))
        return cls(
            account_id=os.environ["R2_ACCOUNT_ID"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            bucket=os.environ["R2_BUCKET"],
            public_base_url=os.environ["R2_PUBLIC_BASE_URL"],
            key_prefix=key_prefix,
            apps_json_key=apps_json_key,
            region=os.getenv("R2_REGION", DEFAULT_REGION),
        )

    # ── key / URL helpers ────────────────────────────────────────────────

    def ipa_key(self, slug: str, version: str, filename: str) -> str:
        """Versioned object key for a signed IPA: ``apps/<slug>/<version>/<file>``."""
        return f"{self.key_prefix}/{slug}/{version}/{filename}"

    def icon_key(self, slug: str) -> str:
        return f"{self.key_prefix}/{slug}/icon.png"

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
        """Delete versioned IPA objects under ``apps/<slug>/`` no longer referenced.

        The whitelist is derived from the *current* apps.json reference set (not
        from time), so a version is only ever deleted once nothing points at it.
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
            # S3 delete_objects accepts up to 1000 keys per call; our volumes are tiny.
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in stale]},
            )
            deleted.extend(stale)
            for key in stale:
                print(f"[info] Deleted stale object: {key}")
        return deleted


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
