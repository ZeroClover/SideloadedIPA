"""Package-engine migration tests for current root-only tasks."""

from __future__ import annotations

import hashlib
import plistlib
import shutil
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.config import load_configuration
from sideloadedipa.domain import (
    BundleGraph,
    BundleNode,
    BundleNodeKind,
    CertificateIdentity,
    CertificateMaterial,
    ExpectedNodeEntitlements,
    ProfileManifestEntry,
    ProfileType,
    ProvisioningProfile,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningEngine,
    SigningPlan,
    SigningResult,
    Task,
    VerificationResult,
    normalize_entitlements,
)
from sideloadedipa.errors import ConfigurationError, ErrorCode
from sideloadedipa.profile_storage import build_profile_manifest, profile_relative_path
from sideloadedipa.signing_service import PackageSigningRequest, execute_package_signing

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
BACKEND_IDENTITY = SigningBackendIdentity(
    "fixture",
    "1",
    "a" * 64,
    "1",
    (
        SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
        SigningBackendFeature.RECURSIVE_SIGNING,
    ),
)


def write_source(path: Path, source_bundle_id: str) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Payload/Upstream.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": source_bundle_id,
                    "CFBundleExecutable": "Upstream",
                }
            ),
        )
        archive.writestr("Payload/Upstream.app/Upstream", b"executable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class CopyBackend:
    def sign(
        self,
        plan: SigningPlan,
        source: Path,
        output: Path,
        certificate: CertificateMaterial,
    ) -> SigningResult:
        del certificate
        shutil.copy2(source, output)
        return SigningResult(
            plan.plan_sha256,
            PurePosixPath(output.name),
            hashlib.sha256(output.read_bytes()).hexdigest(),
            plan.backend,
            (),
            0.1,
        )


@dataclass
class PassingVerifier:
    def verify(self, plan: SigningPlan, signed_ipa: Path) -> VerificationResult:
        return VerificationResult(
            plan.plan_sha256,
            hashlib.sha256(signed_ipa.read_bytes()).hexdigest(),
            True,
            (),
            "b" * 64,
        )


def request_for(task: Task, tmp_path: Path) -> PackageSigningRequest:
    source = tmp_path / f"{task.slug}-source.ipa"
    source_bundle_id = f"com.upstream.{task.slug.lower()}"
    source_sha256 = write_source(source, source_bundle_id)
    root = PurePosixPath("Payload/Upstream.app")
    graph = BundleGraph(
        root,
        (
            BundleNode(
                root,
                BundleNodeKind.APP,
                0,
                root / "Upstream",
                hashlib.sha256(b"executable").hexdigest(),
                source_bundle_id=source_bundle_id,
            ),
        ),
        source_sha256,
        "c" * 64,
    )
    application_identifier = f"PREFIX.{task.bundle_id}"
    entitlements = normalize_entitlements({"application-identifier": application_identifier})
    profile_path = profile_relative_path(task.task_name, task.bundle_id)
    profile = ProvisioningProfile(
        "PROFILE_ROOT",
        f"{task.app_name} Dev",
        ProfileType.IOS_APP_DEVELOPMENT,
        task.bundle_id,
        application_identifier,
        "TEAMID1234",
        "d" * 64,
        ("device",),
        NOW,
        NOW + timedelta(days=90),
        "e" * 64,
        profile_path,
        entitlements.values,
    )
    manifest = build_profile_manifest(
        task_name=task.task_name,
        snapshot_sha256="snapshot",
        entries=(
            ProfileManifestEntry(
                task.bundle_id,
                "BUNDLE_ROOT",
                profile.resource_id,
                "CERTIFICATE",
                profile_path,
                profile.profile_sha256,
                "f" * 64,
                profile.expires_at,
            ),
        ),
    )
    identity = CertificateIdentity(
        "CERTIFICATE",
        "TEAMID1234",
        "1234ABCD",
        "0" * 64,
        "d" * 64,
        NOW + timedelta(days=90),
    )
    material = CertificateMaterial(identity, tmp_path / "certificate.pem", tmp_path / "key.pem")
    return PackageSigningRequest(
        replace(task, signing_engine=SigningEngine.PACKAGE),
        graph,
        manifest,
        (profile,),
        material,
        (ExpectedNodeEntitlements(root, entitlements.values, entitlements.sha256),),
        BACKEND_IDENTITY,
        CopyBackend(),
        PassingVerifier(),
        source,
        tmp_path / f"{task.slug}.ipa",
    )


@pytest.mark.parametrize(
    "task_name",
    ["JHenTai", "Eros FE", "Asspp", "PiliPlus", "StikDebug"],
)
def test_current_root_only_tasks_run_through_package_planner_and_executor(
    tmp_path: Path, task_name: str
) -> None:
    task = next(
        value
        for value in load_configuration(Path("configs/tasks.toml")).tasks
        if value.task_name == task_name
    )
    request = request_for(task, tmp_path)

    result = execute_package_signing(request)

    assert len(result.plan.nodes) == 1
    assert result.plan.nodes[0].target_bundle_id == task.bundle_id
    assert result.execution.verification.passed
    with zipfile.ZipFile(request.destination_ipa) as archive:
        document = plistlib.loads(archive.read("Payload/Upstream.app/Info.plist"))
    assert document["CFBundleIdentifier"] == task.bundle_id


def test_legacy_engine_cannot_enter_package_route(tmp_path: Path) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    request = replace(request_for(task, tmp_path), task=task)

    with pytest.raises(ConfigurationError) as caught:
        execute_package_signing(request)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert not request.destination_ipa.exists()
