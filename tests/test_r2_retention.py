"""Durable retirement and bounded R2 I/O, with no external services."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from sideloadedipa.adapters.publication import R2PublicationGateway
from sideloadedipa.errors import DomainError
from tests.test_r2_store import _store

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
OLD = "apps/example/1.0/old.ipa"
NEW = "apps/example/1.0/new.ipa"
STATE = "site/apps.json.gc.json"


class Bucket:
    def __init__(self, keys: tuple[str, ...] = (OLD, NEW)) -> None:
        self.objects: dict[str, bytes] = dict.fromkeys(keys, b"ipa")
        self.deleted: list[str] = []
        self.client = MagicMock()
        self.client.get_object.side_effect = self.get
        self.client.put_object.side_effect = self.put
        self.client.get_paginator.return_value.paginate.side_effect = self.list
        self.client.delete_objects.side_effect = self.delete
        self.store = _store(self.client)

    def get(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> None:
        self.objects[Key] = Body

    def list(self, *, Bucket: str, Prefix: str) -> list[dict]:
        # Every object is a separate page: pagination must not drop anything.
        return [{"Contents": [{"Key": key}]} for key in self.objects if key.startswith(Prefix)]

    def delete(self, *, Bucket: str, Delete: dict) -> dict:
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)
            self.deleted.append(obj["Key"])
        return {}

    def retired(self) -> dict:
        return json.loads(self.objects[STATE])["retired"]


def test_retirement_survives_process_restart_and_waits_full_48_hours() -> None:
    bucket = Bucket()
    assert bucket.store.cleanup_stale(["example"], {NEW}, now=NOW) == []
    assert bucket.retired() == {OLD: NOW.isoformat()}
    restarted = _store(bucket.client)
    assert restarted.cleanup_stale(["example"], {NEW}, now=NOW + timedelta(hours=47)) == []
    assert restarted.cleanup_stale([], {NEW}, now=NOW + timedelta(hours=48)) == [OLD]
    assert NEW in bucket.objects
    assert bucket.retired() == {}


def test_rolled_back_revival_resets_stale_mark_before_advertisement() -> None:
    bucket = Bucket()
    bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    gateway = R2PublicationGateway(bucket.store, lambda: True)
    gateway.publish_registry({"apps": [{"ipaUrl": bucket.store.public_url(OLD), "iconUrl": ""}]})
    assert bucket.retired() == {}
    gateway.restore_registry({"apps": [{"ipaUrl": bucket.store.public_url(NEW)}]})
    later = NOW + timedelta(days=4)
    assert bucket.store.cleanup_stale(["example"], {NEW}, now=later) == []
    assert bucket.retired() == {OLD: later.isoformat()}


def test_referenced_again_clears_old_retirement_and_restarts_grace() -> None:
    bucket = Bucket()
    bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    bucket.store.cleanup_stale(["example"], {OLD, NEW}, now=NOW + timedelta(days=3))
    assert bucket.retired() == {}
    assert bucket.store.cleanup_stale(["example"], {NEW}, now=NOW + timedelta(days=4)) == []
    assert bucket.retired() == {OLD: (NOW + timedelta(days=4)).isoformat()}


def test_only_managed_selected_objects_enter_retirement() -> None:
    keys = (
        OLD,
        "apps/manual/1/manual.ipa",
        "apps/example/notes.txt",
        "apps/example/backups/notes.zip",
        "apps/example/manual.png",
        "apps/example/../bad.ipa",
        "apps/example/sub/nested/bad.ipa",
        "apps/example/icon-aaaaaaaaaaaa.png",
        "apps/example/icon.png",
    )
    bucket = Bucket(keys)
    bucket.store.cleanup_stale(["example"], set(), now=NOW)
    expected = {OLD, "apps/example/icon-aaaaaaaaaaaa.png", "apps/example/icon.png"}
    assert set(bucket.retired()) == expected
    assert (
        set(bucket.store.cleanup_stale(["example"], set(), now=NOW + timedelta(days=3))) == expected
    )
    assert set(bucket.objects) == set(keys) - expected | {STATE}


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"version": 2, "retired": {}},
        {"version": 1, "retired": {OLD: "bad"}},
        {"version": 1, "retired": {OLD: "2020-01-01"}},
        {"version": 1, "retired": {"outside/file.ipa": NOW.isoformat()}},
        {"version": 1, "retired": {"apps/bad slug/1/a.ipa": NOW.isoformat()}},
        {"version": 1, "retired": {OLD: 42}},
    ],
)
def test_corrupt_state_fails_closed(document: object) -> None:
    bucket = Bucket()
    bucket.objects[STATE] = json.dumps(document).encode()
    with pytest.raises(DomainError):
        bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    assert bucket.deleted == []
    bucket.client.put_object.assert_not_called()


@pytest.mark.parametrize("code", ["NoSuchBucket", "404", "AccessDenied"])
def test_storage_errors_are_not_missing_json(code: str) -> None:
    bucket = Bucket()
    bucket.client.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
    with pytest.raises(ClientError):
        bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    assert bucket.deleted == []


@pytest.mark.parametrize("operation", ["put_object", "listing"])
def test_failure_before_deletion_keeps_artifacts(operation: str) -> None:
    bucket = Bucket()
    target = (
        bucket.client.put_object
        if operation == "put_object"
        else bucket.client.get_paginator.return_value.paginate
    )
    target.side_effect = OSError("offline")
    with pytest.raises(OSError):
        bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    assert bucket.deleted == []


def test_partial_deletion_failure_keeps_retirement_state_for_retry() -> None:
    bucket = Bucket()
    bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    bucket.client.delete_objects.side_effect = lambda **kwargs: {
        "Errors": [{"Key": OLD, "Code": "AccessDenied"}]
    }
    with pytest.raises(DomainError, match="incomplete"):
        bucket.store.cleanup_stale(["example"], {NEW}, now=NOW + timedelta(days=3))
    assert OLD in bucket.retired()
    bucket.client.delete_objects.side_effect = bucket.delete
    assert bucket.store.cleanup_stale(["example"], {NEW}, now=NOW + timedelta(days=4)) == [OLD]


def test_state_write_failure_after_delete_is_recoverable() -> None:
    bucket = Bucket()
    bucket.store.cleanup_stale(["example"], {NEW}, now=NOW)
    bucket.client.put_object.side_effect = OSError("offline")
    with pytest.raises(OSError):
        bucket.store.cleanup_stale(["example"], {NEW}, now=NOW + timedelta(days=3))
    assert OLD not in bucket.objects
    assert OLD in bucket.retired()
    bucket.client.put_object.side_effect = bucket.put
    bucket.store.cleanup_stale(["example"], {NEW}, now=NOW + timedelta(days=4))
    assert bucket.retired() == {}


def test_delete_batches_at_s3_limit() -> None:
    bucket = Bucket()
    bucket.store.delete_keys([str(i) for i in range(2001)])
    assert [
        len(call.kwargs["Delete"]["Objects"])
        for call in bucket.client.delete_objects.call_args_list
    ] == [1000, 1000, 1]


@pytest.mark.parametrize("slug", ["..", ".", "", "../other", "a/b"])
def test_invalid_cleanup_scope_is_rejected(slug: str) -> None:
    bucket = Bucket()
    with pytest.raises(ValueError):
        bucket.store.cleanup_stale([slug], set(), now=NOW)
    assert bucket.deleted == []


def test_naive_observation_time_is_rejected() -> None:
    with pytest.raises(ValueError):
        Bucket().store.cleanup_stale(["example"], set(), now=NOW.replace(tzinfo=None))


def test_streaming_digest_uses_bounded_reads_and_closes_body() -> None:
    data = b"a" * (3 * 1024 * 1024 + 1)
    body = MagicMock(wraps=io.BytesIO(data))
    bucket = Bucket()
    bucket.client.get_object.return_value = {"Body": body}
    bucket.client.get_object.side_effect = None
    assert bucket.store.object_sha256(OLD) == hashlib.sha256(data).hexdigest()
    assert {call.args for call in body.read.call_args_list} == {(1024 * 1024,)}
    body.close.assert_called_once()


@pytest.mark.parametrize("operation", ["object_sha256", "download_json"])
def test_failed_read_closes_response(operation: str) -> None:
    body = MagicMock()
    body.read.side_effect = OSError("interrupted")
    bucket = Bucket()
    bucket.client.get_object.side_effect = None
    bucket.client.get_object.return_value = {"Body": body}
    with pytest.raises(OSError):
        getattr(bucket.store, operation)(OLD)
    body.close.assert_called_once()


@pytest.mark.parametrize("version", ["", ".", "..", "../other", "a/b", "a\\b", "a\x00b"])
def test_invalid_ipa_key_component_is_rejected(version: str) -> None:
    with pytest.raises(DomainError):
        _store().ipa_key("example", version, "App.ipa")


def test_encoded_public_urls_preserve_reference_identity() -> None:
    store = _store()
    key = "apps/example/1+2/My App.ipa"
    url = store.public_url(key)
    assert url.endswith("/1%2B2/My%20App.ipa")
    assert store.key_from_url(url + "?download=1") == key
    assert store.key_from_url("https://ipa.zeroclover.io.attacker/" + key) is None
