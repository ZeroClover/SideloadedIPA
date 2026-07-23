"""Shared pytest fixtures for test suite."""

import hashlib
import plistlib
import shutil
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Generator

import pytest


@pytest.fixture
def thin_macho_bytes() -> Callable[[], bytes]:
    """Build a minimal arm64 MH_EXECUTE accepted by the production LIEF probe."""

    def build() -> bytes:
        return bytes.fromhex(
            "cffaedfe" "0c000001" "00000000" "02000000" "00000000" "00000000" "00000000" "00000000"
        )

    return build


@pytest.fixture
def profile_factory() -> Callable[..., object]:
    """Build a valid provisioning profile with focused per-test overrides."""

    from sideloadedipa.domain import ProfileType, ProvisioningProfile, normalize_entitlements

    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    entitlements = normalize_entitlements({"application-identifier": "TEAMID1234.io.example.app"})
    base = ProvisioningProfile(
        "PROFILE",
        "Example Development",
        ProfileType.IOS_APP_DEVELOPMENT,
        "io.example.app",
        "TEAMID1234.io.example.app",
        "TEAMID1234",
        "a" * 64,
        ("device",),
        now,
        now + timedelta(days=90),
        "b" * 64,
        Path("Example/profile.mobileprovision"),
        entitlements.values,
    )

    def build(**overrides: object) -> ProvisioningProfile:
        return replace(base, **overrides)

    return build


@pytest.fixture
def plan_factory() -> Callable[..., object]:
    """Build a one-node signing plan with focused per-test overrides."""

    from pathlib import PurePosixPath

    from sideloadedipa.domain import (
        BundleNodeKind,
        SigningBackendIdentity,
        SigningNodePlan,
        SigningPlan,
        normalize_entitlements,
    )

    values = normalize_entitlements({"application-identifier": "TEAMID1234.io.example.app"})
    node = SigningNodePlan(
        PurePosixPath("Payload/App.app"),
        PurePosixPath("Payload/App.app/App"),
        BundleNodeKind.APP,
        0,
        "io.example.app",
        "PROFILE",
        PurePosixPath("Example/profile.mobileprovision"),
        "b" * 64,
        values.values,
        values.sha256,
    )
    base = SigningPlan(
        "Example",
        "0" * 64,
        "1" * 64,
        "a" * 64,
        SigningBackendIdentity("fixture", "1", "2" * 64, "1"),
        (node,),
        "3" * 64,
    )

    def build(**overrides: object) -> SigningPlan:
        return replace(base, **overrides)

    return build


def publication_candidate(artifact: Path):  # type: ignore[no-untyped-def]
    """Build a publication candidate shared by report and R2 adapter tests."""

    from sideloadedipa.domain import (
        BundleNodeKind,
        PublicationCandidate,
        SigningBackendIdentity,
        SigningNodePlan,
        SigningPlan,
        VerificationFinding,
        normalize_entitlements,
    )
    from sideloadedipa.verification import (
        build_verification_result,
        required_verification_checks,
    )

    values = normalize_entitlements({"application-identifier": "TEAM.io.example.app"})
    plan = SigningPlan(
        "Example",
        "0" * 64,
        "1" * 64,
        "2" * 64,
        SigningBackendIdentity("fixture", "1", "3" * 64, "1"),
        (
            SigningNodePlan(
                PurePosixPath("Payload/App.app"),
                PurePosixPath("Payload/App.app/App"),
                BundleNodeKind.APP,
                0,
                "io.example.app",
                "PROFILE",
                PurePosixPath("Example/profile.mobileprovision"),
                "4" * 64,
                values.values,
                values.sha256,
            ),
        ),
        "a" * 64,
    )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    verification = build_verification_result(
        plan,
        digest,
        tuple(
            VerificationFinding(path, check.replace("*", "arm64"), True)
            for path, check in required_verification_checks(plan)
        ),
    )
    return PublicationCandidate(
        "Example",
        "example",
        "Example",
        "io.example.app",
        "1.2.3",
        "Example.ipa",
        str(artifact),
        digest,
        "https://cdn.example/apps/example/icon.png",
        True,
        plan,
        verification,
    )


@dataclass
class FixtureCopyBackend:
    called: bool = False

    def sign(self, plan, source, output, certificate):  # type: ignore[no-untyped-def]
        from sideloadedipa.domain import SigningNodeResult, SigningResult

        del certificate
        self.called = True
        shutil.copy2(source, output)
        output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
        return SigningResult(
            plan.plan_sha256,
            PurePosixPath(output.name),
            output_sha256,
            plan.backend,
            tuple(
                SigningNodeResult(
                    node.source_path,
                    output_sha256,
                    node.profile_sha256,
                    node.expected_entitlements_sha256,
                    0.0,
                )
                for node in plan.nodes
            ),
            0.1,
        )


@dataclass
class FixturePassingVerifier:
    calls: int = 0

    def verify(self, plan, signed_ipa):  # type: ignore[no-untyped-def]
        self.calls += 1
        from sideloadedipa.domain import VerificationFinding
        from sideloadedipa.verification import (
            build_verification_result,
            required_verification_checks,
        )

        findings = tuple(
            VerificationFinding(path, check.replace("*", "arm64"), True)
            for path, check in required_verification_checks(plan)
        )
        return build_verification_result(
            plan,
            hashlib.sha256(signed_ipa.read_bytes()).hexdigest(),
            findings,
        )


def package_request(task, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Build the shared single-bundle package signing request."""

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
        normalize_entitlements,
    )
    from sideloadedipa.signing.profile_storage import (
        build_profile_manifest,
        profile_relative_path,
    )
    from sideloadedipa.signing.service import PackageSigningRequest

    source = tmp_path / f"{task.slug}-source.ipa"
    source_bundle_id = f"com.upstream.{task.slug.lower()}"
    with zipfile.ZipFile(source, "w") as archive:
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
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
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
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
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
        now,
        now + timedelta(days=90),
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
        now + timedelta(days=90),
    )
    backend_identity = SigningBackendIdentity(
        "fixture",
        "1",
        "a" * 64,
        "1",
        (
            SigningBackendFeature.PER_PROFILE_ENTITLEMENTS,
            SigningBackendFeature.RECURSIVE_SIGNING,
        ),
    )
    return PackageSigningRequest(
        task,
        graph,
        manifest,
        (profile,),
        CertificateMaterial(identity, tmp_path / "certificate.pem", tmp_path / "key.pem"),
        (ExpectedNodeEntitlements(root, entitlements.values, entitlements.sha256),),
        backend_identity,
        FixtureCopyBackend(),
        FixturePassingVerifier(),
        source,
        tmp_path / f"{task.slug}.ipa",
    )


def production_command(
    tmp_path: Path,
    name,
    *task_names: str,
    run_id: str = "run-one",
    publish: bool = False,
):  # type: ignore[no-untyped-def]
    from sideloadedipa.application import CommandRequest, OutputFormat

    return CommandRequest(
        name,
        tmp_path / "configs/tasks.toml",
        task_names,
        OutputFormat.JSON,
        apply=name.value == "sync",
        publish=publish,
        run_id=run_id,
    )


def production_dependencies(tmp_path: Path):  # type: ignore[no-untyped-def]
    from sideloadedipa.pipeline.environment import PipelineEnvironmentDependencies
    from sideloadedipa.pipeline.production import ProductionPipelineDependencies

    return ProductionPipelineDependencies(
        package=PipelineEnvironmentDependencies(
            output_root=tmp_path / "signed",
            cache_root=tmp_path / "cache",
            profile_root=tmp_path / "profiles",
            environment={
                "ZSIGN_BIN": str(tmp_path / "zsign"),
                "ZSIGN_SHA256": "a" * 64,
                "APPLE_DEV_CERT_P12_ENCODED": "ZmFrZQ==",
                "APPLE_DEV_CERT_PASSWORD": "secret",
            },
        ),
        manifest_root=tmp_path / "pipeline",
        report_root=tmp_path / "reports",
    )


def production_source_context(tmp_path: Path, task, graph=None):  # type: ignore[no-untyped-def]
    from sideloadedipa.domain import BundleGraph, SourceAsset
    from sideloadedipa.pipeline.inspection import ResolvedSource
    from sideloadedipa.pipeline.production import SourceContext
    from sideloadedipa.sources import DownloadedSource

    path = tmp_path / f"{task.slug}.ipa"
    path.write_bytes(task.task_name.encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bundle_graph = graph or BundleGraph(PurePosixPath("Payload/App.app"), (), digest, "b" * 64)
    resolved = ResolvedSource(
        f"https://example.invalid/{task.slug}.ipa",
        digest,
        {
            "asset_id": task.task_name,
            "asset_name": path.name,
            "release_tag": "v1",
            "published_at": "2026-07-22T00:00:00Z",
        },
        path.stat().st_size,
    )
    return SourceContext(
        task,
        resolved,
        DownloadedSource(path, path.stat().st_size, digest),
        SourceAsset(
            task.task_name,
            path.name,
            resolved.url,
            "v1",
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            PurePosixPath(path.name),
            digest,
        ),
        bundle_graph,
    )


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary work directory structure."""
    cache_dir = tmp_path / "work" / "cache"
    cache_old_dir = tmp_path / "work" / "cache-old"
    cache_dir.mkdir(parents=True)
    cache_old_dir.mkdir(parents=True)
    yield tmp_path


