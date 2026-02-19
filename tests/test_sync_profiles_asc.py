"""Tests for scripts/sync_profiles_asc.py."""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from sync_profiles_asc import extract_compatible_device_ids, find_profile


def _device(device_id: str, device_class: str) -> dict[str, Any]:
    return {
        "id": device_id,
        "attributes": {
            "deviceClass": device_class,
        },
    }


class TestExtractCompatibleDeviceIds:
    """Tests for device-class filtering in profile creation."""

    def test_filters_to_iphone_and_ipad(self) -> None:
        devices = [
            _device("d-iphone", "IPHONE"),
            _device("d-ipad", "IPAD"),
            _device("d-watch", "APPLE_WATCH"),
            _device("d-tv", "APPLE_TV"),
            _device("d-mac", "MAC"),
        ]

        compatible_ids = extract_compatible_device_ids(devices)

        assert compatible_ids == ["d-iphone", "d-ipad"]

    def test_raises_when_no_compatible_devices(self) -> None:
        devices = [
            _device("d-watch", "APPLE_WATCH"),
            _device("d-tv", "APPLE_TV"),
        ]

        with pytest.raises(RuntimeError, match="No compatible devices found"):
            extract_compatible_device_ids(devices)


class TestFindProfile:
    """Tests for profile lookup by name and bundle ID."""

    def test_matches_when_list_contains_bundle_relationship(self) -> None:
        list_response = {
            "data": [
                {
                    "id": "profile-1",
                    "attributes": {"name": "My App Dev"},
                    "relationships": {"bundleId": {"data": {"id": "bundle-1"}}},
                }
            ]
        }

        with patch("sync_profiles_asc.run_asc", return_value=list_response):
            profile = find_profile("My App Dev", "bundle-1")

        assert profile is not None
        assert profile["id"] == "profile-1"

    def test_falls_back_to_bundle_relationship_lookup(self) -> None:
        list_response = {
            "data": [
                {
                    "id": "profile-1",
                    "attributes": {"name": "My App Dev"},
                    "relationships": {"bundleId": {"links": {"self": "ignored"}}},
                }
            ]
        }
        relationship_response = {"data": {"type": "bundleIds", "id": "bundle-1"}}

        with patch(
            "sync_profiles_asc.run_asc",
            side_effect=[list_response, relationship_response],
        ) as mock_run_asc:
            profile = find_profile("My App Dev", "bundle-1")

        assert profile is not None
        assert profile["id"] == "profile-1"
        assert mock_run_asc.call_count == 2
        assert mock_run_asc.call_args_list[1].args[0] == [
            "profiles",
            "relationships",
            "bundle-id",
            "--id",
            "profile-1",
        ]

    def test_returns_none_when_bundle_id_does_not_match(self) -> None:
        list_response = {
            "data": [
                {
                    "id": "profile-1",
                    "attributes": {"name": "My App Dev"},
                    "relationships": {"bundleId": {"links": {"self": "ignored"}}},
                }
            ]
        }
        relationship_response = {"data": {"type": "bundleIds", "id": "bundle-other"}}

        with patch(
            "sync_profiles_asc.run_asc",
            side_effect=[list_response, relationship_response],
        ):
            profile = find_profile("My App Dev", "bundle-1")

        assert profile is None
