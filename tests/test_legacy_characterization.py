"""Compatibility locks for profile sync and reviewed release evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import sync_profiles_asc

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "baseline"


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


def _verify_asset(path: Path, expected: dict[str, Any]) -> None:
    assert path.stat().st_size == expected["size"], f"unexpected size for {path.name}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected["sha256"], f"unexpected SHA-256 for {path.name}"


def test_livecontainer_release_fixture_records_reviewed_assets() -> None:
    baseline = _load_json("livecontainer-3.8.0.json")

    assert baseline["tag"] == "3.8.0"
    assert baseline["commit"] == "e370a92dfc03ce109ebce00ed4a7cfc64ad1c801"
    assert [(asset["name"], asset["size"], asset["sha256"]) for asset in baseline["assets"]] == [
        (
            "LiveContainer.ipa",
            4_707_271,
            "b6fea95e30083382e29ffef88fa1aaa40b5069e1112e5307d490dab04648bba6",
        ),
        (
            "LiveContainer+SideStore.ipa",
            35_403_538,
            "97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425",
        ),
    ]


def test_livecontainer_fixture_setup_rejects_drift(tmp_path: Path) -> None:
    expected = _load_json("livecontainer-3.8.0.json")["assets"][0]
    candidate = tmp_path / expected["name"]
    candidate.write_bytes(b"not the reviewed release asset")

    with pytest.raises(AssertionError, match="unexpected size"):
        _verify_asset(candidate, expected)


def test_profile_download_decodes_current_asc_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "Legacy.mobileprovision"
    payload = base64.b64encode(b"sanitized profile bytes").decode()
    run_asc = MagicMock(return_value={"data": {"attributes": {"profileContent": payload}}})
    monkeypatch.setattr(sync_profiles_asc, "run_asc", run_asc)

    sync_profiles_asc.download_profile("profile-1", output)

    assert output.read_bytes() == b"sanitized profile bytes"
    run_asc.assert_called_once_with(["profiles", "view", "--id", "profile-1"])


def test_profile_regeneration_deletes_then_creates_then_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_profiles_asc, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(sync_profiles_asc, "find_bundle_id", lambda _: "bundle-resource")
    monkeypatch.setattr(
        sync_profiles_asc, "find_profile", lambda _name, _bundle: {"id": "old-profile"}
    )
    delete_profile = MagicMock()
    create_profile = MagicMock(return_value="new-profile")
    download_profile = MagicMock()
    monkeypatch.setattr(sync_profiles_asc, "delete_profile", delete_profile)
    monkeypatch.setattr(sync_profiles_asc, "create_profile", create_profile)
    monkeypatch.setattr(sync_profiles_asc, "download_profile", download_profile)

    sync_profiles_asc.sync_profile(
        {
            "task_name": "Legacy",
            "app_name": "Legacy App",
            "bundle_id": "com.example.legacy",
        },
        ["certificate-1"],
        ["device-1"],
    )

    delete_profile.assert_called_once_with("old-profile")
    create_profile.assert_called_once_with(
        "Legacy App Dev", "bundle-resource", ["certificate-1"], ["device-1"]
    )
    download_profile.assert_called_once_with("new-profile", tmp_path / "Legacy.mobileprovision")


def test_missing_profile_is_created_with_enabled_ios_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_profiles_asc, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(sync_profiles_asc, "find_bundle_id", lambda _: "bundle-resource")
    monkeypatch.setattr(sync_profiles_asc, "find_profile", lambda *_: None)
    monkeypatch.setattr(
        sync_profiles_asc,
        "fetch_devices",
        lambda: [
            {"id": "iphone", "attributes": {"deviceClass": "IPHONE"}},
            {"id": "watch", "attributes": {"deviceClass": "APPLE_WATCH"}},
        ],
    )
    monkeypatch.setattr(sync_profiles_asc, "fetch_certificates", lambda: [{"id": "cert"}])
    create_profile = MagicMock(return_value="profile")
    download_profile = MagicMock()
    monkeypatch.setattr(sync_profiles_asc, "create_profile", create_profile)
    monkeypatch.setattr(sync_profiles_asc, "download_profile", download_profile)

    sync_profiles_asc.download_existing_profile(
        {
            "task_name": "Legacy",
            "app_name": "Legacy App",
            "bundle_id": "com.example.legacy",
        }
    )

    create_profile.assert_called_once_with(
        "Legacy App Dev", "bundle-resource", ["cert"], ["iphone"]
    )
    download_profile.assert_called_once_with("profile", tmp_path / "Legacy.mobileprovision")


def test_missing_bundle_id_fails_before_profile_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync_profiles_asc, "find_bundle_id", lambda _: None)
    create_profile = MagicMock()
    monkeypatch.setattr(sync_profiles_asc, "create_profile", create_profile)

    with pytest.raises(SystemExit) as error:
        sync_profiles_asc.sync_profile(
            {
                "task_name": "Legacy",
                "app_name": "Legacy App",
                "bundle_id": "com.example.missing",
            },
            ["cert"],
            ["device"],
        )

    assert error.value.code == 1
    create_profile.assert_not_called()


def test_github_output_contract_is_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    sync_profiles_asc.write_github_output("devices_changed", "false")
    sync_profiles_asc.write_github_output("missing_profiles", '["Legacy"]')

    assert output.read_text().splitlines() == [
        "devices_changed=false",
        'missing_profiles=["Legacy"]',
    ]


def test_current_release_audit_requires_one_match_per_production_task() -> None:
    audit = _load_json("production-release-audit.json")

    assert audit["effective_glob"] == "*.ipa"
    assert [task["task_name"] for task in audit["tasks"]] == [
        "JHenTai",
        "Eros FE",
        "Asspp",
        "PiliPlus",
        "StikDebug",
    ]
    assert all(len(task["matches"]) == 1 for task in audit["tasks"])
