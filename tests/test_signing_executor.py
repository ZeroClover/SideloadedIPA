"""Tests for copy-on-write signing execution and atomic promotion."""

from __future__ import annotations

import hashlib
import plistlib
import shutil
import stat
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    CertificateIdentity,
    CertificateMaterial,
    Diagnostic,
    DiagnosticSeverity,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    SigningResult,
    VerificationFinding,
    VerificationResult,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.signing_executor import execute_signing_plan, package_workspace_ipa
from sideloadedipa.verification import build_verification_result, required_verification_checks


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_ipa(path: Path) -> None:
    info = plistlib.dumps(
        {
            "CFBundleIdentifier": "com.example.source",
            "CFBundleExecutable": "Example",
        }
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/Example.app/Info.plist", info)
        executable = zipfile.ZipInfo("Payload/Example.app/Example")
        executable.create_system = 3
        executable.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(executable, b"executable")


def plan_for(source: Path) -> SigningPlan:
    empty = normalize_entitlements({})
    return SigningPlan(
        task_name="Example",
        source_ipa_sha256=sha256(source),
        graph_sha256="a" * 64,
        certificate_sha256="b" * 64,
        backend=SigningBackendIdentity("fixture", "1", "c" * 64, "1"),
        nodes=(
            SigningNodePlan(
                source_path=PurePosixPath("Payload/Example.app"),
                executable_path=PurePosixPath("Payload/Example.app/Example"),
                kind=BundleNodeKind.APP,
                order=0,
                target_bundle_id="io.example.target",
                profile_resource_id="PROFILE",
                profile_path=PurePosixPath("Example/PROFILE.mobileprovision"),
                profile_sha256="d" * 64,
                expected_entitlements=empty.values,
                expected_entitlements_sha256=empty.sha256,
            ),
        ),
        plan_sha256="e" * 64,
    )


def certificate(tmp_path: Path) -> CertificateMaterial:
    identity = CertificateIdentity(
        "CERTIFICATE",
        "TEAMID1234",
        "1234ABCD",
        "a" * 64,
        "b" * 64,
        datetime.now(timezone.utc) + timedelta(days=30),
    )
    return CertificateMaterial(identity, tmp_path / "certificate.pem", tmp_path / "key.pem")


@dataclass
class CopyingBackend:
    seen_source: Path | None = None
    fail: bool = False

    def sign(
        self,
        plan: SigningPlan,
        source: Path,
        output: Path,
        material: CertificateMaterial,
    ) -> SigningResult:
        del material
        self.seen_source = source
        if self.fail:
            output.write_bytes(b"partial")
            raise RuntimeError("signing failed")
        shutil.copy2(source, output)
        return SigningResult(
            plan.plan_sha256,
            PurePosixPath(output.name),
            sha256(output),
            plan.backend,
            (),
            0.1,
        )


@dataclass
class ContractBreakingBackend:
    mismatch: str

    def sign(
        self,
        plan: SigningPlan,
        source: Path,
        output: Path,
        material: CertificateMaterial,
    ) -> SigningResult:
        del material
        if self.mismatch != "output-missing":
            shutil.copy2(source, output)
        result = SigningResult(
            plan.plan_sha256,
            PurePosixPath(output.name),
            sha256(output) if output.exists() else "0" * 64,
            plan.backend,
            (),
            0.1,
        )
        if self.mismatch == "plan":
            return replace(result, plan_sha256="0" * 64)
        if self.mismatch == "backend":
            return replace(result, backend=replace(plan.backend, name="wrong"))
        if self.mismatch == "path":
            return replace(result, output_path=PurePosixPath("wrong.ipa"))
        if self.mismatch == "digest":
            return replace(result, output_sha256="0" * 64)
        return result


@dataclass
class InspectingVerifier:
    destination: Path
    prior_content: bytes
    passed: bool = True
    wrong_digest: bool = False
    called: bool = False

    def verify(self, plan: SigningPlan, signed_ipa: Path) -> VerificationResult:
        self.called = True
        assert self.destination.read_bytes() == self.prior_content
        artifact_sha256 = "0" * 64 if self.wrong_digest else sha256(signed_ipa)
        findings = tuple(
            VerificationFinding(
                path,
                check.replace("*", "arm64"),
                self.passed,
                diagnostics=(
                    ()
                    if self.passed
                    else (
                        Diagnostic(
                            "verification.failed",
                            DiagnosticSeverity.ERROR,
                            "fixture failure",
                        ),
                    )
                ),
            )
            for path, check in required_verification_checks(plan)
        )
        return build_verification_result(plan, artifact_sha256, findings)


def test_signs_copy_rewrites_identifier_and_promotes_after_verification(tmp_path: Path) -> None:
    source = tmp_path / "downloaded.ipa"
    destination = tmp_path / "result.ipa"
    source_ipa(source)
    original = source.read_bytes()
    destination.write_bytes(b"previous verified artifact")
    backend = CopyingBackend()
    verifier = InspectingVerifier(destination, destination.read_bytes())

    result = execute_signing_plan(
        plan=plan_for(source),
        source_ipa=source,
        destination_ipa=destination,
        certificate=certificate(tmp_path),
        backend=backend,
        verifier=verifier,
    )

    assert source.read_bytes() == original
    assert backend.seen_source is not None and backend.seen_source != source
    assert verifier.called
    assert result.signing.output_path == PurePosixPath(destination.name)
    assert result.signing.output_sha256 == sha256(destination)
    assert result.rewrites[0].source_bundle_id == "com.example.source"
    with zipfile.ZipFile(destination) as archive:
        info = plistlib.loads(archive.read("Payload/Example.app/Info.plist"))
        assert info["CFBundleIdentifier"] == "io.example.target"
        executable = archive.getinfo("Payload/Example.app/Example")
        assert (executable.external_attr >> 16) & 0o777 == 0o755
    assert not (tmp_path / ".sideloadedipa-signing").exists()


@pytest.mark.parametrize("failure", ["backend", "verification", "digest"])
def test_failure_preserves_source_and_previous_artifact(tmp_path: Path, failure: str) -> None:
    source = tmp_path / "downloaded.ipa"
    destination = tmp_path / "result.ipa"
    source_ipa(source)
    original = source.read_bytes()
    previous = b"previous verified artifact"
    destination.write_bytes(previous)
    backend = CopyingBackend(fail=failure == "backend")
    verifier = InspectingVerifier(
        destination,
        previous,
        passed=failure != "verification",
        wrong_digest=failure == "digest",
    )

    expected_error = RuntimeError if failure == "backend" else DomainError
    with pytest.raises(expected_error) as caught:
        execute_signing_plan(
            plan=plan_for(source),
            source_ipa=source,
            destination_ipa=destination,
            certificate=certificate(tmp_path),
            backend=backend,
            verifier=verifier,
        )

    assert source.read_bytes() == original
    assert destination.read_bytes() == previous
    assert verifier.called is (failure != "backend")
    assert not (tmp_path / ".sideloadedipa-signing").exists()
    if failure == "verification":
        assert isinstance(caught.value, DomainError)
        details = dict(caught.value.safe_details)
        failed_checks = details["failed_checks"]
        assert "Payload/Example.app:bundle-identifier" in failed_checks
        assert (
            "Payload/Example.app:bundle-identifier:verification.failed:$"
            in details["failed_diagnostics"]
        )


def test_rejects_source_digest_mismatch_before_creating_workspace(tmp_path: Path) -> None:
    source = tmp_path / "downloaded.ipa"
    source_ipa(source)
    signing_plan = plan_for(source)
    source.write_bytes(b"changed")

    with pytest.raises(DomainError) as caught:
        execute_signing_plan(
            plan=signing_plan,
            source_ipa=source,
            destination_ipa=tmp_path / "result.ipa",
            certificate=certificate(tmp_path),
            backend=CopyingBackend(),
            verifier=InspectingVerifier(tmp_path / "result.ipa", b""),
        )

    assert caught.value.code is ErrorCode.SIGNING_VERIFICATION_FAILED
    assert not (tmp_path / ".sideloadedipa-signing").exists()


def test_packaging_is_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "Payload/App.app").mkdir(parents=True)
    executable = workspace / "Payload/App.app/App"
    executable.write_bytes(b"content")
    executable.chmod(0o755)

    first = tmp_path / "first.ipa"
    second = tmp_path / "second.ipa"
    package_workspace_ipa(workspace, first)
    package_workspace_ipa(workspace, second)

    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("mismatch", ["output-missing", "plan", "backend", "path", "digest"])
def test_rejects_backend_result_contract_mismatches(tmp_path: Path, mismatch: str) -> None:
    source = tmp_path / "downloaded.ipa"
    destination = tmp_path / "result.ipa"
    source_ipa(source)
    previous = b"previous verified artifact"
    destination.write_bytes(previous)

    with pytest.raises(DomainError) as caught:
        execute_signing_plan(
            plan=plan_for(source),
            source_ipa=source,
            destination_ipa=destination,
            certificate=certificate(tmp_path),
            backend=ContractBreakingBackend(mismatch),
            verifier=InspectingVerifier(destination, previous),
        )

    assert caught.value.code is ErrorCode.SIGNING_VERIFICATION_FAILED
    assert destination.read_bytes() == previous
    assert not (tmp_path / ".sideloadedipa-signing").exists()


def test_rejects_identical_source_and_destination(tmp_path: Path) -> None:
    source = tmp_path / "downloaded.ipa"
    source_ipa(source)

    with pytest.raises(DomainError, match="must be different"):
        execute_signing_plan(
            plan=plan_for(source),
            source_ipa=source,
            destination_ipa=source,
            certificate=certificate(tmp_path),
            backend=CopyingBackend(),
            verifier=InspectingVerifier(source, source.read_bytes()),
        )


def test_packaging_rejects_symbolic_links(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "outside"
    target.write_bytes(b"content")
    (workspace / "link").symlink_to(target)

    with pytest.raises(DomainError) as caught:
        package_workspace_ipa(workspace, tmp_path / "output.ipa")

    assert caught.value.code is ErrorCode.WORKSPACE_INVALID
