"""Tests for the read-only backend qualification prerequisite probe."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from qualify_backend_prerequisites import (
    ProfileEvidence,
    QualificationError,
    ensure_bundle_resources,
    ensure_common_contract,
    exact_bundle_resources,
    profile_bundle_resource_id,
)


def test_exact_bundle_resources_requires_one_exact_match() -> None:
    bundles = [
        {"id": "root-id", "attributes": {"identifier": "example.root"}},
        {"id": "extension-id", "attributes": {"identifier": "example.root.extension"}},
    ]

    assert exact_bundle_resources(
        bundles, {"root": "example.root", "extension": "example.root.extension"}
    ) == {"root": "root-id", "extension": "extension-id"}


def test_exact_bundle_resources_reports_all_missing_roles() -> None:
    with pytest.raises(QualificationError, match="root:example.root has 0 exact App IDs"):
        exact_bundle_resources([], {"root": "example.root"})


def test_ensure_bundle_resources_creates_only_missing_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = [{"id": "root-id", "attributes": {"identifier": "example.root"}}]
    refreshed = {
        "data": [
            *existing,
            {"id": "extension-id", "attributes": {"identifier": "example.extension"}},
        ]
    }
    calls: list[list[str]] = []

    def fake_run_json(args: list[str]) -> dict:
        calls.append(args)
        if args[:2] == ["bundle-ids", "create"]:
            return {"data": {"id": "extension-id"}}
        return refreshed

    monkeypatch.setattr("qualify_backend_prerequisites.run_json", fake_run_json)

    result = ensure_bundle_resources(
        existing,
        {"root": "example.root", "extension": "example.extension"},
        {"root": "Root", "extension": "Extension"},
        apply=True,
    )

    assert result == {"root": "root-id", "extension": "extension-id"}
    create_calls = [args for args in calls if args[:2] == ["bundle-ids", "create"]]
    assert create_calls == [
        [
            "bundle-ids",
            "create",
            "--identifier",
            "example.extension",
            "--name",
            "Extension",
            "--platform",
            "IOS",
        ]
    ]


def test_profile_bundle_resource_id_reads_embedded_relationship() -> None:
    profile = {"relationships": {"bundleId": {"data": {"id": "bundle-id"}}}}

    assert profile_bundle_resource_id(profile) == "bundle-id"


def _evidence(
    role: str, certificate: str = "certificate", devices: frozenset[str] = frozenset({"d1"})
) -> ProfileEvidence:
    return ProfileEvidence(
        role=role,
        target_bundle_id=f"example.{role}",
        profile_id=f"profile-{role}",
        profile_sha256=f"profile-hash-{role}",
        certificate_sha256=(certificate,),
        device_ids=devices,
        entitlement_keys=("application-identifier",),
        app_groups=("group.example.shared",),
    )


def test_common_contract_requires_p12_certificate_and_shared_resources() -> None:
    result = ensure_common_contract([_evidence("root"), _evidence("extension")], "certificate")

    assert result == {
        "common_device_count": 1,
        "common_app_groups": ["group.example.shared"],
        "p12_certificate_sha256": "certificate",
    }


def test_common_contract_rejects_certificate_mismatch() -> None:
    with pytest.raises(QualificationError, match="P12 certificate"):
        ensure_common_contract([_evidence("root", certificate="other")], "certificate")
