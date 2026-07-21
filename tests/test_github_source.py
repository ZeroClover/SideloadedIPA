"""Tests for exact-one GitHub release asset selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.sources import GitHubReleaseAsset, select_release_asset

FIXTURES = Path(__file__).parent / "fixtures" / "baseline"


def asset(name: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 1,
        "name": name,
        "browser_download_url": f"https://example.com/{name}",
        "size": 1024,
    }
    value.update(overrides)
    return value


def test_selects_one_asset_and_records_complete_evidence() -> None:
    selected = select_release_asset(
        {
            "assets": [
                asset("App.ipa", id=42, size=2048, digest="sha256:abc"),
                asset("notes.txt"),
            ]
        },
        "*.ipa",
    )

    assert selected == GitHubReleaseAsset(
        index=0,
        asset_id="42",
        name="App.ipa",
        browser_download_url="https://example.com/App.ipa",
        size=2048,
        digest="sha256:abc",
    )


def test_five_production_audits_remain_unambiguous_with_default_glob() -> None:
    audit = json.loads((FIXTURES / "production-release-audit.json").read_text())

    for index, task in enumerate(audit["tasks"]):
        names = [*task["matches"], "release-notes.txt"]
        release = {"assets": [asset(name, id=index) for name in names]}

        selected = select_release_asset(release, audit["effective_glob"])

        assert selected.name == task["matches"][0], task["task_name"]


def test_livecontainer_default_is_ambiguous_and_exact_selector_succeeds() -> None:
    baseline = json.loads((FIXTURES / "livecontainer-3.8.0.json").read_text())
    release = {
        "assets": [
            asset(
                value["name"],
                id=value["id"],
                size=value["size"],
                browser_download_url=value["url"],
                digest=f"sha256:{value['sha256']}",
            )
            for value in baseline["assets"]
        ]
    }

    with pytest.raises(DomainError) as caught:
        select_release_asset(release, "*.ipa")

    assert caught.value.code is ErrorCode.SOURCE_ASSET_AMBIGUOUS
    assert caught.value.safe_details == (
        ("pattern", "*.ipa"),
        (
            "matching_names",
            ("LiveContainer.ipa", "LiveContainer+SideStore.ipa"),
        ),
    )
    selected = select_release_asset(release, "LiveContainer.ipa")
    assert selected.name == "LiveContainer.ipa"
    assert selected.digest == f"sha256:{baseline['assets'][0]['sha256']}"


def test_no_match_lists_every_available_name() -> None:
    with pytest.raises(DomainError) as caught:
        select_release_asset({"assets": [asset("README.txt"), asset("App.zip")]}, "*.ipa")

    assert caught.value.code is ErrorCode.SOURCE_ASSET_NOT_FOUND
    assert caught.value.safe_details == (
        ("pattern", "*.ipa"),
        ("available_names", ("README.txt", "App.zip")),
    )


@pytest.mark.parametrize(
    ("release", "field"),
    [
        ({}, "assets"),
        ({"assets": ["invalid"]}, "assets[0]"),
        ({"assets": [{"name": ""}]}, "assets[0].name"),
        ({"assets": [asset("App.ipa", id=True)]}, "assets[0].id"),
        (
            {"assets": [asset("App.ipa", browser_download_url="")]},
            "assets[0].browser_download_url",
        ),
        ({"assets": [asset("App.ipa", size=-1)]}, "assets[0].size"),
        ({"assets": [asset("App.ipa", digest=42)]}, "assets[0].digest"),
    ],
)
def test_rejects_malformed_release_evidence(release: dict[str, object], field: str) -> None:
    with pytest.raises(DomainError) as caught:
        select_release_asset(release, "*.ipa")

    assert caught.value.code is ErrorCode.SOURCE_RELEASE_INVALID
    assert caught.value.safe_details == (("field", field),)
