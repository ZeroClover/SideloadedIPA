"""Tests for exact-one GitHub release asset selection."""

from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from sideloadedipa.errors import AdapterError, DomainError, ErrorCode
from sideloadedipa.sources import (
    GitHubReleaseAsset,
    fetch_github_release,
)
from sideloadedipa.sources import github as github_source
from sideloadedipa.sources import (
    github_repository_name,
    select_release_asset,
)

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


class Response(BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_fetch_latest_release_uses_current_versioned_github_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def open_request(request: Request, timeout: float) -> Response:
        requests.append(request)
        assert timeout == 30
        return Response(json.dumps({"tag_name": "v1", "assets": []}).encode())

    monkeypatch.setattr(github_source, "urlopen", open_request)

    release = fetch_github_release(
        "https://github.com/example/application.git", token="private-token"
    )

    assert release["tag_name"] == "v1"
    assert (
        requests[0].full_url == "https://api.github.com/repos/example/application/releases/latest"
    )
    headers = {key.lower(): value for key, value in requests[0].headers.items()}
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2026-03-10"
    assert headers["authorization"] == "Bearer private-token"


def test_fetch_prerelease_skips_drafts_and_falls_back_to_published_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [
        {"tag_name": "draft", "draft": True, "prerelease": True},
        {"tag_name": "preview", "draft": False, "prerelease": True},
        {"tag_name": "stable", "draft": False, "prerelease": False},
    ]
    monkeypatch.setattr(
        github_source,
        "urlopen",
        lambda request, timeout: Response(json.dumps(releases).encode()),
    )

    release = fetch_github_release("git@github.com:example/application.git", use_prerelease=True)

    assert release["tag_name"] == "preview"


def test_repository_name_and_adapter_failures_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        github_repository_name("https://github.com/example/application/") == "example/application"
    )
    with pytest.raises(DomainError):
        github_repository_name("https://example.com/example/application")

    def fail(request: Request, timeout: float) -> Response:
        raise HTTPError(request.full_url, 403, "secret body", Message(), None)

    monkeypatch.setattr(github_source, "urlopen", fail)
    with pytest.raises(AdapterError) as caught:
        fetch_github_release("https://github.com/example/application", token="private-token")

    assert caught.value.safe_details == (
        ("adapter", "github-rest"),
        ("operation", "read-release"),
        ("status", 403),
    )
    assert "private-token" not in str(caught.value)


def test_selects_one_asset_and_records_complete_evidence() -> None:
    digest = f"sha256:{'A' * 64}"
    selected = select_release_asset(
        {
            "assets": [
                asset("App.ipa", id=42, size=2048, digest=digest),
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
        digest=digest.lower(),
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
        ({"assets": [asset("App.ipa", digest="sha256:abc")]}, "assets[0].digest"),
        ({"assets": [asset("App.ipa", digest=f"sha512:{'a' * 64}")]}, "assets[0].digest"),
    ],
)
def test_rejects_malformed_release_evidence(release: dict[str, object], field: str) -> None:
    with pytest.raises(DomainError) as caught:
        select_release_asset(release, "*.ipa")

    assert caught.value.code is ErrorCode.SOURCE_RELEASE_INVALID
    assert caught.value.safe_details == (("field", field),)
