"""Tests for the read-only backend qualification prerequisite probe."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from qualify_backend_prerequisites import (
    ProfileEvidence,
    QualificationError,
    certificate_content,
    delete_legacy_bundle_ids,
    delete_legacy_profiles,
    ensure_bundle_resources,
    ensure_common_contract,
    ensure_profiles,
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


def test_delete_legacy_bundle_ids_requires_exact_legacy_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_json(args: list[str], *, allow_empty: bool = False) -> dict:
        calls.append(args)
        return {}

    monkeypatch.setattr("qualify_backend_prerequisites.run_json", fake_run_json)

    with pytest.raises(QualificationError, match="refusing to delete"):
        delete_legacy_bundle_ids(
            [
                {
                    "id": "process-id",
                    "attributes": {
                        "identifier": "io.zeroclover.app.livecontainer.LiveProcess",
                        "name": "Unrelated App ID",
                    },
                }
            ]
        )

    assert calls == []


def test_delete_legacy_bundle_ids_tolerates_already_recreated_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_json(args: list[str], *, allow_empty: bool = False) -> dict:
        calls.append(args)
        return {}

    monkeypatch.setattr("qualify_backend_prerequisites.run_json", fake_run_json)
    delete_legacy_bundle_ids(
        [
            {
                "id": "process-id",
                "attributes": {
                    "identifier": "io.zeroclover.app.livecontainer.LiveProcess",
                    "name": "LiveContainer LiveProcess",
                },
            }
        ]
    )

    assert calls == []


def test_delete_legacy_bundle_ids_deletes_only_exact_legacy_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run_json(args: list[str], *, allow_empty: bool = False) -> dict:
        calls.append((args, allow_empty))
        return {}

    monkeypatch.setattr("qualify_backend_prerequisites.run_json", fake_run_json)
    delete_legacy_bundle_ids(
        [
            {
                "id": "process-id",
                "attributes": {
                    "identifier": "io.zeroclover.app.livecontainer.LiveProcess",
                    "name": "SideloadedIPA LiveContainer Qualification LiveProcess",
                },
            }
        ]
    )

    assert calls == [(["bundle-ids", "delete", "--id", "process-id", "--confirm"], True)]


def test_delete_legacy_profiles_matches_name_and_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run_json(args: list[str], *, allow_empty: bool = False) -> dict:
        calls.append((args, allow_empty))
        return {}

    monkeypatch.setattr("qualify_backend_prerequisites.run_json", fake_run_json)
    delete_legacy_profiles(
        [
            {
                "id": "legacy-profile",
                "attributes": {"name": "SideloadedIPA LiveContainer Qualification Root Dev"},
                "relationships": {"bundleId": {"data": {"id": "root-bundle"}}},
            },
            {
                "id": "unrelated-profile",
                "attributes": {"name": "SideloadedIPA LiveContainer Qualification Root Dev"},
                "relationships": {"bundleId": {"data": {"id": "other-bundle"}}},
            },
        ],
        {"root": "root-bundle"},
    )

    assert calls == [(["profiles", "delete", "--id", "legacy-profile", "--confirm"], True)]


def test_profile_bundle_resource_id_reads_embedded_relationship() -> None:
    profile = {"relationships": {"bundleId": {"data": {"id": "bundle-id"}}}}

    assert profile_bundle_resource_id(profile) == "bundle-id"


def test_certificate_content_decodes_base64_der() -> None:
    assert (
        certificate_content({"attributes": {"certificateContent": "Y2VydGlmaWNhdGU="}})
        == b"certificate"
    )


def test_ensure_profiles_creates_each_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    refreshed = {
        "data": [
            {
                "id": "root-profile",
                "attributes": {"profileType": "IOS_APP_DEVELOPMENT", "profileState": "ACTIVE"},
                "relationships": {"bundleId": {"data": {"id": "root-bundle"}}},
            },
            {
                "id": "extension-profile",
                "attributes": {"profileType": "IOS_APP_DEVELOPMENT", "profileState": "ACTIVE"},
                "relationships": {"bundleId": {"data": {"id": "extension-bundle"}}},
            },
        ]
    }

    def fake_run_json(args: list[str]) -> dict:
        calls.append(args)
        if args[:2] == ["profiles", "create"]:
            return {"data": {"id": "created"}}
        return refreshed

    monkeypatch.setattr("qualify_backend_prerequisites.run_json", fake_run_json)
    monkeypatch.setattr(
        "qualify_backend_prerequisites.PROFILE_NAMES",
        {"root": "Root Dev", "extension": "Extension Dev"},
    )

    result = ensure_profiles(
        [],
        {"root": "root-bundle", "extension": "extension-bundle"},
        "certificate-id",
        ["device-2", "device-1"],
        apply=True,
    )

    assert result == {"root": "root-profile", "extension": "extension-profile"}
    create_calls = [args for args in calls if args[:2] == ["profiles", "create"]]
    assert len(create_calls) == 2
    assert all(args[args.index("--device") + 1] == "device-1,device-2" for args in create_calls)


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
