"""Tests for exact signing-plan-driven Bundle ID rewrites."""

from __future__ import annotations

import plistlib
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.bundle_transform import rewrite_bundle_identifiers
from sideloadedipa.domain import (
    BundleNodeKind,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode

ROOT = PurePosixPath("Payload/App.app")
SHARE = ROOT / "PlugIns/Share.appex"
FRAMEWORK = SHARE / "Frameworks/Kit.framework"


def node(
    path: PurePosixPath,
    kind: BundleNodeKind,
    order: int,
    target_bundle_id: str | None,
):
    expected = normalize_entitlements(
        {"application-identifier": f"PREFIX.{target_bundle_id}"}
        if target_bundle_id is not None
        else {}
    )
    return SigningNodePlan(
        source_path=path,
        kind=kind,
        order=order,
        target_bundle_id=target_bundle_id,
        profile_resource_id="PROFILE" if target_bundle_id is not None else None,
        profile_path=(path / "profile.mobileprovision") if target_bundle_id is not None else None,
        profile_sha256="f" * 64 if target_bundle_id is not None else None,
        expected_entitlements=expected.values,
        expected_entitlements_sha256=expected.sha256,
    )


def plan() -> SigningPlan:
    return SigningPlan(
        task_name="Example",
        source_ipa_sha256="a" * 64,
        graph_sha256="b" * 64,
        certificate_sha256="c" * 64,
        backend=SigningBackendIdentity("zsign", "version", "d" * 64, "1"),
        nodes=(
            node(FRAMEWORK, BundleNodeKind.FRAMEWORK, 0, None),
            node(SHARE, BundleNodeKind.APP_EXTENSION, 1, "io.custom.share"),
            node(ROOT, BundleNodeKind.APP, 2, "io.example.app"),
        ),
        plan_sha256="e" * 64,
    )


def write_info(
    workspace: Path,
    path: PurePosixPath,
    bundle_id: str,
    output_format: plistlib.PlistFormat,
) -> Path:
    destination = workspace.joinpath(*path.parts, "Info.plist")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(
        plistlib.dumps(
            {"CFBundleIdentifier": bundle_id, "CFBundleExecutable": "Executable"},
            fmt=output_format,
            sort_keys=False,
        )
    )
    destination.chmod(0o640)
    return destination


def test_rewrites_root_and_explicit_nested_override_while_preserving_format(
    tmp_path: Path,
) -> None:
    root_info = write_info(tmp_path, ROOT, "com.upstream.app", plistlib.FMT_BINARY)
    share_info = write_info(tmp_path, SHARE, "com.upstream.app.Share", plistlib.FMT_XML)

    evidence = rewrite_bundle_identifiers(tmp_path, plan())

    assert plistlib.loads(root_info.read_bytes())["CFBundleIdentifier"] == "io.example.app"
    assert plistlib.loads(share_info.read_bytes())["CFBundleIdentifier"] == "io.custom.share"
    assert root_info.read_bytes().startswith(b"bplist00")
    assert share_info.read_bytes().startswith(b"<?xml")
    assert root_info.stat().st_mode & 0o777 == 0o640
    assert [(value.source_bundle_id, value.target_bundle_id) for value in evidence] == [
        ("com.upstream.app.Share", "io.custom.share"),
        ("com.upstream.app", "io.example.app"),
    ]
    assert not list(tmp_path.rglob(".tmp-info-*"))


def test_validates_every_plist_before_writing_any_identifier(tmp_path: Path) -> None:
    root_info = write_info(tmp_path, ROOT, "com.upstream.app", plistlib.FMT_BINARY)
    original_root = root_info.read_bytes()
    invalid_share = tmp_path.joinpath(*SHARE.parts, "Info.plist")
    invalid_share.parent.mkdir(parents=True)
    invalid_share.write_bytes(b"not a plist")

    with pytest.raises(DomainError) as caught:
        rewrite_bundle_identifiers(tmp_path, plan())

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
    assert root_info.read_bytes() == original_root


def test_rejects_unsafe_or_non_profile_target_nodes(tmp_path: Path) -> None:
    configured = plan()
    traversal = replace(
        configured.nodes[-1],
        source_path=PurePosixPath("../Outside.app"),
    )
    with pytest.raises(DomainError) as unsafe:
        rewrite_bundle_identifiers(tmp_path, replace(configured, nodes=(traversal,)))
    assert unsafe.value.code is ErrorCode.SIGNING_PLAN_INVALID

    invalid_framework = replace(configured.nodes[0], target_bundle_id="io.example.framework")
    with pytest.raises(DomainError) as profile_free:
        rewrite_bundle_identifiers(tmp_path, replace(configured, nodes=(invalid_framework,)))
    assert profile_free.value.code is ErrorCode.SIGNING_PLAN_INVALID


@pytest.mark.parametrize(
    "document",
    [
        ["not", "a", "dictionary"],
        {"CFBundleExecutable": "Executable"},
    ],
)
def test_rejects_invalid_info_plist_shape(tmp_path: Path, document: object) -> None:
    root_info = tmp_path.joinpath(*ROOT.parts, "Info.plist")
    root_info.parent.mkdir(parents=True)
    root_info.write_bytes(plistlib.dumps(document))
    root_plan = replace(plan(), nodes=(plan().nodes[-1],))

    with pytest.raises(DomainError) as caught:
        rewrite_bundle_identifiers(tmp_path, root_plan)

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
