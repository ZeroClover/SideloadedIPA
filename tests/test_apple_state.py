"""Tests for normalized read-only Apple signing state."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from sideloadedipa.adapters.apple import (
    AppleStateCollector,
    AscResponse,
    canonical_apple_snapshot_json,
)
from sideloadedipa.apple_state_probe import redacted_summary
from sideloadedipa.domain import FrozenJsonObject, freeze_json
from sideloadedipa.errors import AdapterError, ErrorCode

FIXTURE = Path(__file__).parent / "fixtures" / "asc" / "apple-state.json"


class FixtureClient:
    def __init__(self, fixture: dict[str, object]) -> None:
        self.fixture = fixture
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def run_json(
        self,
        args: tuple[str, ...],
        *,
        paginate: bool = False,
        allow_empty: bool = False,
    ) -> AscResponse:
        self.calls.append((args, paginate))
        if args[:2] == ("bundle-ids", "list"):
            value = self.fixture["bundle_ids"]
        elif args[:3] == ("bundle-ids", "capabilities", "list"):
            capabilities = self.fixture["capabilities"]
            assert isinstance(capabilities, dict)
            value = capabilities[args[-1]]
        elif args[:2] == ("certificates", "list"):
            value = self.fixture["certificates"]
        elif args[:2] == ("devices", "list"):
            value = self.fixture["devices"]
        elif args[:2] == ("profiles", "list"):
            value = self.fixture["profiles"]
        else:
            details = self.fixture["profile_details"]
            assert isinstance(details, dict)
            value = details[args[args.index("--id") + 1]]
        frozen = freeze_json(value)
        assert isinstance(frozen, FrozenJsonObject)
        return AscResponse(frozen, ("asc", *args), 0.01)


def fixture() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text())
    assert isinstance(value, dict)
    return value


def test_collects_sorted_redacted_snapshot_from_one_read_session() -> None:
    client = FixtureClient(fixture())

    snapshot = AppleStateCollector(client).collect()
    serialized = canonical_apple_snapshot_json(snapshot)
    document = json.loads(serialized)

    assert [value.identifier for value in snapshot.bundle_ids] == [
        "io.example.app",
        "io.example.app.child",
    ]
    assert snapshot.capabilities[0].capability_type == "APP_GROUPS"
    assert snapshot.capabilities[0].bundle_resource_id == "BUNDLE_ROOT"
    assert (
        snapshot.certificates[0].certificate_sha256
        == hashlib.sha256(b"certificate-fixture").hexdigest()
    )
    assert snapshot.devices[0].udid_sha256 == hashlib.sha256(b"UDID_PRIVATE_FIXTURE").hexdigest()
    assert snapshot.profiles[0].bundle_resource_id == "BUNDLE_ROOT"
    assert snapshot.profiles[0].certificate_resource_ids == ("CERTIFICATE_ONE",)
    assert snapshot.profiles[0].device_resource_ids == ("DEVICE_ONE",)
    assert snapshot.profiles[0].profile_sha256 == hashlib.sha256(b"profile-fixture").hexdigest()
    assert document["snapshot_sha256"] == snapshot.snapshot_sha256
    assert (
        snapshot.snapshot_sha256
        == hashlib.sha256(
            json.dumps(
                {key: value for key, value in document.items() if key != "snapshot_sha256"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert b"UDID_PRIVATE_FIXTURE" not in serialized
    assert b"certificate-fixture" not in serialized
    assert b"profile-fixture" not in serialized
    assert redacted_summary(snapshot) == {
        "schema_version": 1,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "counts": {
            "bundle_ids": 2,
            "capabilities": 1,
            "certificates": 1,
            "devices": 1,
            "profiles": 1,
        },
    }

    assert client.calls == [
        (("bundle-ids", "list"), True),
        (("bundle-ids", "capabilities", "list", "--bundle", "BUNDLE_ROOT"), True),
        (("bundle-ids", "capabilities", "list", "--bundle", "BUNDLE_CHILD"), True),
        (
            (
                "certificates",
                "list",
                "--certificate-type",
                "IOS_DEVELOPMENT,DEVELOPMENT",
            ),
            True,
        ),
        (("devices", "list", "--platform", "IOS", "--status", "ENABLED"), True),
        (("profiles", "list", "--profile-type", "IOS_APP_DEVELOPMENT"), True),
        (
            (
                "profiles",
                "view",
                "--id",
                "PROFILE_ONE",
                "--include",
                "bundleId,certificates,devices",
            ),
            False,
        ),
    ]


def test_snapshot_digest_is_independent_of_api_list_order() -> None:
    first_fixture = fixture()
    second_fixture = deepcopy(first_fixture)
    bundle_document = second_fixture["bundle_ids"]
    assert isinstance(bundle_document, dict)
    bundles = bundle_document["data"]
    assert isinstance(bundles, list)
    bundles.reverse()

    first = AppleStateCollector(FixtureClient(first_fixture)).collect()
    second = AppleStateCollector(FixtureClient(second_fixture)).collect()

    assert first == second


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (
            lambda value: value.update({"bundle_ids": {"data": {}}}),
            "bundle_ids.data",
        ),
        (
            lambda value: value["certificates"]["data"][0]["attributes"].update(
                {"certificateContent": "not-base64"}
            ),
            "certificateContent",
        ),
        (
            lambda value: value["profile_details"]["PROFILE_ONE"]["data"].update(
                {"id": "PROFILE_OTHER"}
            ),
            "profile.data.id",
        ),
    ],
)
def test_rejects_incomplete_or_malformed_state(mutate: object, field: str) -> None:
    value = fixture()
    assert callable(mutate)
    mutate(value)

    with pytest.raises(AdapterError) as caught:
        AppleStateCollector(FixtureClient(value)).collect()

    assert caught.value.code is ErrorCode.ADAPTER_RESPONSE_INVALID
    assert field in str(caught.value.safe_details)
