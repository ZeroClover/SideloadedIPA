"""Opt-in inventory checks against checksum-pinned LiveContainer IPAs."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import (
    discover_bundle_graph,
    discover_bundle_structure,
    extract_ipa_safely,
)
from sideloadedipa.sources import DEFAULT_DOWNLOAD_POLICY, download_source_asset

BASELINE_PATH = Path(__file__).parent / "fixtures" / "baseline" / "livecontainer-3.8.0.json"
STANDARD_IDS = {
    "com.kdt.livecontainer",
    "com.kdt.livecontainer.LaunchAppExtension",
    "com.kdt.livecontainer.LiveProcess",
    "com.kdt.livecontainer.ShareExtension",
}
EXPECTED_IDS = {
    "LiveContainer.ipa": STANDARD_IDS,
    "LiveContainer+SideStore.ipa": STANDARD_IDS | {"com.kdt.livecontainer.LiveWidget"},
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION") != "1",
        reason="set SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION=1 to download pinned IPAs",
    ),
]


def load_reviewed_assets() -> list[dict[str, object]]:
    baseline = json.loads(BASELINE_PATH.read_text())
    assert baseline["repository"] == "LiveContainer/LiveContainer"
    assert baseline["tag"] == "3.8.0"
    assets = baseline["assets"]
    assert isinstance(assets, list)
    assert {asset["name"] for asset in assets} == set(EXPECTED_IDS)
    return assets


@pytest.mark.parametrize("asset", load_reviewed_assets(), ids=lambda value: str(value["name"]))
def test_pinned_livecontainer_inventory(asset: dict[str, object], tmp_path: Path) -> None:
    name = str(asset["name"])
    url = str(asset["url"])
    expected_sha256 = str(asset["sha256"])
    expected_size = int(str(asset["size"]))
    assert url.startswith("https://github.com/LiveContainer/LiveContainer/releases/download/3.8.0/")

    downloaded = download_source_asset(
        url,
        tmp_path / name,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        policy=replace(DEFAULT_DOWNLOAD_POLICY, timeout_seconds=180),
    )
    assert downloaded.size == expected_size
    extracted = tmp_path / "extracted"
    extract_ipa_safely(downloaded.path, extracted)

    structure = discover_bundle_structure(extracted)
    profile_nodes = [node for node in structure if node.profile_bearing]
    assert {node.source_bundle_id for node in profile_nodes} == EXPECTED_IDS[name]
    assert len(profile_nodes) == len(EXPECTED_IDS[name])

    if name == "LiveContainer.ipa":
        graph = discover_bundle_graph(extracted, downloaded.sha256)
        assert sum(node.profile_bearing for node in graph.nodes) == 4
    else:
        with pytest.raises(DomainError) as caught:
            discover_bundle_graph(extracted, downloaded.sha256)
        assert caught.value.code is ErrorCode.INVENTORY_ENTITLEMENTS_INVALID
        assert "LiveWidgetExtension.appex" in dict(caught.value.safe_details)["path"]
