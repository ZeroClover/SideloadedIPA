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
    BundleRule,
    CertificateIdentity,
    CertificateMaterial,
    EntitlementMode,
    EntitlementPolicy,
    ExpectedNodeEntitlements,
    IdentifierStrategy,
    ProfileManifestEntry,
    ProfileType,
    ProvisioningProfile,
    SigningBackendFeature,
    SigningBackendIdentity,
    SigningEngine,
    SigningPlan,
    SigningPolicy,
    SigningResult,
    Task,
    UnknownProfileBundlePolicy,
    VerificationFinding,
    VerificationResult,
    normalize_entitlements,
)
from sideloadedipa.errors import ConfigurationError, DomainError, ErrorCode
from sideloadedipa.profile_storage import build_profile_manifest, profile_relative_path
from sideloadedipa.signing_service import (
    PackageSigningRequest,
    build_package_signing_request,
    execute_package_signing,
)
from sideloadedipa.verification import build_verification_result, required_verification_checks

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
    called: bool = False

    def sign(
        self,
        plan: SigningPlan,
        source: Path,
        output: Path,
        certificate: CertificateMaterial,
    ) -> SigningResult:
        del certificate
        self.called = True
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
        findings = tuple(
            VerificationFinding(path, check.replace("*", "arm64"), True)
            for path, check in required_verification_checks(plan)
        )
        return build_verification_result(
            plan, hashlib.sha256(signed_ipa.read_bytes()).hexdigest(), findings
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


def test_composes_current_root_task_from_profile_entitlements(tmp_path: Path) -> None:
    task = next(
        value
        for value in load_configuration(Path("configs/tasks.toml")).tasks
        if value.task_name == "JHenTai"
    )
    fixture = request_for(task, tmp_path)

    request = build_package_signing_request(
        task=fixture.task,
        graph=fixture.graph,
        profile_manifest=fixture.profile_manifest,
        profiles=fixture.profiles,
        certificate=fixture.certificate,
        backend_identity=fixture.backend_identity,
        backend=fixture.backend,
        verifier=fixture.verifier,
        source_ipa=fixture.source_ipa,
        destination_ipa=fixture.destination_ipa,
        repository_root=tmp_path,
    )

    assert request.expected_entitlements[0].values == fixture.profiles[0].entitlements
    assert execute_package_signing(request).execution.verification.passed


def test_composes_reviewed_template_with_typed_placeholders(tmp_path: Path) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    fixture = request_for(task, tmp_path)
    source_bundle_id = fixture.graph.nodes[0].source_bundle_id
    assert source_bundle_id is not None
    template = tmp_path / "configs/signing/root.plist"
    template.parent.mkdir(parents=True)
    template.write_bytes(
        plistlib.dumps({"application-identifier": "${APP_IDENTIFIER_PREFIX}${TARGET_BUNDLE_ID}"})
    )
    configured = replace(
        fixture.task,
        signing=SigningPolicy(
            IdentifierStrategy.PRESERVE_SOURCE_SUFFIX,
            UnknownProfileBundlePolicy.ERROR,
            ProfileType.IOS_APP_DEVELOPMENT,
            bundles=(
                BundleRule(
                    source_bundle_id,
                    EntitlementPolicy(
                        EntitlementMode.TEMPLATE,
                        PurePosixPath("configs/signing/root.plist"),
                    ),
                    fixture.task.bundle_id,
                    "root",
                ),
            ),
        ),
    )

    request = build_package_signing_request(
        task=configured,
        graph=fixture.graph,
        profile_manifest=fixture.profile_manifest,
        profiles=fixture.profiles,
        certificate=fixture.certificate,
        backend_identity=fixture.backend_identity,
        backend=fixture.backend,
        verifier=fixture.verifier,
        source_ipa=fixture.source_ipa,
        destination_ipa=fixture.destination_ipa,
        repository_root=tmp_path,
    )

    assert dict(request.expected_entitlements[0].values)["application-identifier"] == (
        f"PREFIX.{fixture.task.bundle_id}"
    )


def test_legacy_engine_cannot_enter_package_route(tmp_path: Path) -> None:
    task = replace(
        load_configuration(Path("configs/tasks.toml")).tasks[0],
        signing_engine=SigningEngine.LEGACY,
    )
    request = replace(request_for(task, tmp_path), task=task)

    with pytest.raises(ConfigurationError) as caught:
        execute_package_signing(request)

    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert not request.destination_ipa.exists()


def test_profile_authorization_failure_precedes_backend_and_workspace(tmp_path: Path) -> None:
    task = load_configuration(Path("configs/tasks.toml")).tasks[0]
    request = request_for(task, tmp_path)
    profile = replace(request.profiles[0], entitlements=normalize_entitlements({}).values)
    backend = request.backend
    assert isinstance(backend, CopyBackend)

    with pytest.raises(DomainError) as caught:
        execute_package_signing(replace(request, profiles=(profile,)))

    assert caught.value.code is ErrorCode.APPLE_PROFILE_ENTITLEMENT_UNAUTHORIZED
    assert backend.called is False
    assert not request.destination_ipa.exists()
    assert not (tmp_path / ".sideloadedipa-signing").exists()
