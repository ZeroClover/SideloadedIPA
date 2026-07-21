"""Tests for production package-engine filesystem composition."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import sideloadedipa.package_runner as runner
from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    CertificateIdentity,
    CertificateMaterial,
    EntitlementMode,
    EntitlementPolicy,
    ProfileManifestEntry,
    SigningBackendIdentity,
    SigningEngine,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.profile_storage import build_profile_manifest, profile_relative_path

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def manifest(*certificate_ids: str):
    task_name = "JHenTai"
    entries = tuple(
        ProfileManifestEntry(
            f"io.example.app.{index}",
            f"BUNDLE_{index}",
            f"PROFILE_{index}",
            certificate_id,
            profile_relative_path(task_name, f"io.example.app.{index}"),
            str(index) * 64,
            "d" * 64,
            NOW + timedelta(days=90),
        )
        for index, certificate_id in enumerate(certificate_ids, start=1)
    )
    return build_profile_manifest(task_name=task_name, snapshot_sha256="snapshot", entries=entries)


def material(tmp_path: Path) -> CertificateMaterial:
    identity = CertificateIdentity(
        "CERT_ONE",
        "TEAM",
        "SERIAL",
        "a" * 64,
        "b" * 64,
        NOW + timedelta(days=90),
    )
    return CertificateMaterial(identity, tmp_path / "cert.pem", tmp_path / "key.pem")


def test_requires_source_entitlements_only_for_preserve_source_policy() -> None:
    tasks = load_configuration(Path("configs/tasks.toml")).tasks
    legacy = tasks[0]
    livecontainer = next(task for task in tasks if task.task_name == "LiveContainer")
    assert livecontainer.signing is not None
    preserve_rule = replace(
        livecontainer.signing.bundles[0],
        entitlement_policy=EntitlementPolicy(EntitlementMode.PRESERVE_SOURCE),
    )
    preserve_task = replace(
        livecontainer,
        signing=replace(livecontainer.signing, bundles=(preserve_rule,)),
    )

    assert runner._source_entitlements_required(legacy) is False
    assert runner._source_entitlements_required(livecontainer) is False
    assert runner._source_entitlements_required(preserve_task) is True


def test_inspects_exact_source_digest_in_temporary_workspace(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.ipa"
    source.write_bytes(b"source")
    expected = BundleGraph(PurePosixPath("Payload/App.app"), (), "a" * 64, "b" * 64)

    def extract(path: Path, destination: Path):
        assert path == source
        destination.mkdir()
        return ()

    def discover(path: Path, digest: str, *, allow_missing_code_signature: bool):
        assert path.name == "extracted"
        assert digest == hashlib.sha256(b"source").hexdigest()
        assert allow_missing_code_signature is False
        return expected

    monkeypatch.setattr(runner, "extract_ipa_safely", extract)
    monkeypatch.setattr(runner, "discover_bundle_graph", discover)

    assert runner.inspect_source_graph(source) is expected


def test_wires_synced_inputs_to_one_verified_package_execution(tmp_path: Path, monkeypatch) -> None:
    task = replace(
        load_configuration(Path("configs/tasks.toml")).tasks[0],
        signing_engine=SigningEngine.PACKAGE,
    )
    stored_manifest = manifest("CERT_ONE")
    certificate = material(tmp_path)
    graph = BundleGraph(PurePosixPath("Payload/App.app"), (), "a" * 64, "b" * 64)
    profiles = (SimpleNamespace(),)
    backend_identity = SigningBackendIdentity("zsign", "qualified", "c" * 64, "1")
    calls: dict[str, object] = {}

    monkeypatch.setattr(runner, "load_profile_manifest", lambda root, name: stored_manifest)

    def load_certificate(path, password, *, resource_id, output_directory):
        calls["certificate"] = (path, password, resource_id, output_directory)
        return certificate

    monkeypatch.setattr(runner, "load_p12_certificate_material", load_certificate)

    def load_profiles(**kwargs):
        calls["profiles"] = kwargs
        return profiles

    monkeypatch.setattr(runner, "load_synced_profiles", load_profiles)
    monkeypatch.setattr(runner, "inspect_source_graph", lambda path, *, task: graph)

    class Backend:
        def __init__(self, **kwargs):
            calls["backend"] = kwargs

        def identity(self):
            return backend_identity

    monkeypatch.setattr(runner, "ZsignBackend", Backend)
    monkeypatch.setattr(
        runner,
        "PackageVerifier",
        lambda source, values, now: SimpleNamespace(source=source, profiles=values, now=now),
    )

    request = object()

    def build_request(**kwargs):
        calls["request"] = kwargs
        return request

    result = object()
    monkeypatch.setattr(runner, "build_package_signing_request", build_request)
    monkeypatch.setattr(
        runner, "execute_package_signing", lambda value: result if value is request else None
    )

    actual = runner.run_package_signing(
        task=task,
        source_ipa=tmp_path / "source.ipa",
        destination_ipa=tmp_path / "signed.ipa",
        profile_root=tmp_path / "profiles",
        p12_path=tmp_path / "certificate.p12",
        p12_password="secret",
        private_directory=tmp_path / "private",
        zsign_executable=tmp_path / "zsign",
        zsign_sha256="c" * 64,
        repository_root=tmp_path,
        now=NOW,
    )

    assert actual is result
    assert calls["certificate"][2] == "CERT_ONE"
    assert calls["profiles"]["certificate"] is certificate.identity
    assert calls["request"]["graph"] is graph
    assert calls["request"]["backend_identity"] is backend_identity


def test_rejects_mixed_certificate_manifest_before_private_key_load(
    tmp_path: Path, monkeypatch
) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    monkeypatch.setattr(
        runner,
        "load_profile_manifest",
        lambda root, name: manifest("CERT_ONE", "CERT_TWO"),
    )

    with pytest.raises(DomainError) as caught:
        runner.run_package_signing(
            task=task,
            source_ipa=tmp_path / "source.ipa",
            destination_ipa=tmp_path / "signed.ipa",
            profile_root=tmp_path / "profiles",
            p12_path=tmp_path / "certificate.p12",
            p12_password="secret",
            private_directory=tmp_path / "private",
            zsign_executable=tmp_path / "zsign",
            zsign_sha256="c" * 64,
            repository_root=tmp_path,
            now=NOW,
        )

    assert caught.value.code is ErrorCode.SIGNING_PLAN_INVALID
