#!/usr/bin/env python3
"""Cloudflare R2 storage helpers (S3-compatible API, via boto3).

The bucket holds every publishable artifact of the signing pipeline:

    apps/<slug>/<version>/<sha256>-<App>.ipa  # immutable signed build
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
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode

# Headers for versioned IPA objects: keys are never reused across releases, so
# they can be cached forever. Content-Type follows Apple's OTA deployment guide.
IPA_CONTENT_TYPE = "application/octet-stream"
IPA_CONTENT_DISPOSITION = "attachment"
IPA_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Next owns the registry cache; R2/CDN must not serve a stale promotion.
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
JSON_CACHE_CONTROL = "no-store"
RETIREMENT_GRACE = timedelta(hours=48)

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
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
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
        for part in (slug, version, filename):
            if (
                not part
                or part in {".", ".."}
                or "/" in part
                or "\\" in part
                or any(ord(char) < 32 for char in part)
            ):
                raise DomainError(ErrorCode.PUBLICATION_FAILED, "invalid IPA object key component")
        return f"{self.key_prefix}/{slug}/{version}/{filename}"

    def icon_key(self, slug: str, png_bytes: bytes) -> str:
        """Content-addressed icon key: ``apps/<slug>/icon-<sha12>.png``.

        Derived from the bytes rather than the slug, so re-uploading an
        unchanged icon is a no-op and a changed one gets a fresh, uncached URL.
        """
        digest = hashlib.sha256(png_bytes).hexdigest()[:ICON_DIGEST_LENGTH]
        return f"{self.key_prefix}/{slug}/icon-{digest}.png"

    def public_url(self, key: str) -> str:
        return f"{self.public_base_url}/{quote(key, safe='/')}"

    def key_from_url(self, url: str) -> Optional[str]:
        """Map a public URL back to its object key; ``None`` if not on this bucket."""
        parsed = urlsplit(url)
        base = urlsplit(self.public_base_url)
        prefix = f"{base.path}/"
        if (parsed.scheme, parsed.netloc) == (base.scheme, base.netloc) and parsed.path.startswith(
            prefix
        ):
            return unquote(parsed.path[len(prefix) :])
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

    def object_sha256(self, key: str) -> str:
        """Confirm remote bytes without loading an entire IPA into memory."""
        body = self._client.get_object(Bucket=self.bucket, Key=key)["Body"]
        digest = hashlib.sha256()
        try:
            while chunk := body.read(1024 * 1024):
                digest.update(chunk)
        finally:
            body.close()
        return digest.hexdigest()

    def download_json(self, key: str) -> Optional[dict[str, Any]]:
        """Fetch and parse a JSON object; ``None`` when the key does not exist."""
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise
        body = response["Body"]
        try:
            data = json.loads(body.read().decode("utf-8"))
        finally:
            body.close()
        if not isinstance(data, dict):
            raise DomainError(ErrorCode.PUBLICATION_FAILED, "stored JSON must be an object")
        return data

    # ── durable retired-object cleanup ──────────────────────────────────

    def _managed_key(self, key: str) -> bool:
        prefix = f"{self.key_prefix}/"
        if not key.startswith(prefix):
            return False
        parts = key[len(prefix) :].split("/")
        if any(part in {"", ".", ".."} for part in parts):
            return False
        if not re.fullmatch(r"[A-Za-z0-9._-]+", parts[0]):
            return False
        return (len(parts) == 3 and parts[-1].endswith(".ipa")) or (
            len(parts) == 2 and re.fullmatch(r"icon(?:-[a-f0-9]{12})?\.png", parts[-1]) is not None
        )

    def _retired_keys(self) -> dict[str, datetime]:
        document = self.download_json(f"{self.apps_json_key}.gc.json")
        if document is None:
            return {}
        raw = document.get("retired")
        if document.get("version") != 1 or not isinstance(raw, dict):
            raise DomainError(ErrorCode.PUBLICATION_FAILED, "invalid artifact retirement state")
        retired: dict[str, datetime] = {}
        for key, value in raw.items():
            if not self._managed_key(key) or not isinstance(value, str):
                raise DomainError(ErrorCode.PUBLICATION_FAILED, "invalid artifact retirement entry")
            try:
                timestamp = datetime.fromisoformat(value)
            except ValueError as error:
                raise DomainError(
                    ErrorCode.PUBLICATION_FAILED, "invalid retirement timestamp"
                ) from error
            if timestamp.tzinfo is None:
                raise DomainError(
                    ErrorCode.PUBLICATION_FAILED, "retirement timestamp lacks timezone"
                )
            retired[key] = timestamp
        return retired

    def _save_retired(self, retired: dict[str, datetime]) -> None:
        self.upload_json(
            f"{self.apps_json_key}.gc.json",
            {
                "version": 1,
                "retired": {key: value.isoformat() for key, value in sorted(retired.items())},
            },
        )

    def protect_keys(self, keys: set[str]) -> None:
        """Reset retirement before advertising a previously retired artifact again."""
        retired = self._retired_keys()
        pending = {key: since for key, since in retired.items() if key not in keys}
        if pending != retired:
            self._save_retired(pending)

    def cleanup_stale(
        self,
        slugs: list[str],
        referenced_keys: set[str],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Retire managed objects; delete only after 48 hours of non-reference.

        A single publisher is required. Persist marks before deletion so a
        partial failure can safely resume. Pending slugs remain in scope even
        when a later run selects different tasks. Never use object upload age.
        """
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("retirement observation requires an aware timestamp")
        retired = self._retired_keys()
        prefix = f"{self.key_prefix}/"
        scopes = set(slugs) | {key[len(prefix) :].split("/", 1)[0] for key in retired}
        if any(
            not re.fullmatch(r"[A-Za-z0-9._-]+", slug) or slug in {".", ".."} for slug in scopes
        ):
            raise ValueError("invalid artifact cleanup slug")
        present: set[str] = set()
        paginator = self._client.get_paginator("list_objects_v2")
        for slug in sorted(scopes):
            scope = f"{prefix}{slug}/"
            for page in paginator.paginate(Bucket=self.bucket, Prefix=scope):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.startswith(scope) and self._managed_key(key):
                        present.add(key)
        pending = {key: retired.get(key, observed_at) for key in present - referenced_keys}
        # Do not drop a new timestamp merely because a later deletion fails.
        if pending != retired:
            self._save_retired(pending)
        expired = sorted(
            key for key, since in pending.items() if observed_at - since >= RETIREMENT_GRACE
        )
        self.delete_keys(expired)
        if expired:
            removed = set(expired)
            self._save_retired({key: since for key, since in pending.items() if key not in removed})
        return expired

    def delete_keys(self, keys: list[str]) -> None:
        """Delete in S3-sized batches and reject per-object failures."""
        for offset in range(0, len(keys), 1000):
            batch = keys[offset : offset + 1000]
            response = self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch]},
            )
            if response.get("Errors"):
                raise DomainError(
                    ErrorCode.PUBLICATION_FAILED,
                    "R2 object deletion was incomplete",
                    remediation="retain retirement state and retry cleanup on the next successful run",
                    safe_details=(("attempted_keys", tuple(batch)),),
                )
            for key in batch:
                print(f"[info] Deleted object: {key}")


def main() -> int:  # pragma: no cover - manual smoke helper
    store = R2Store.from_env()
    doc = store.download_json(store.apps_json_key)
    print(json.dumps(doc, indent=2, ensure_ascii=False) if doc else "(apps.json missing)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
