"""Tests for signed IPA graph and protected-payload integrity."""

from __future__ import annotations

import hashlib
import plistlib
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from sideloadedipa.domain import (
    BundleNodeKind,
    SigningBackendIdentity,
    SigningNodePlan,
    SigningPlan,
    VerificationFinding,
    normalize_entitlements,
)
from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.verification import verify_output_integrity


class MarkerMachOProbe:
    def is_macho(self, path: Path) -> bool:
        return path.read_bytes().startswith(b"MACHO")


def info(identifier: str, executable: str, package_type: str, *, version: str = "1") -> bytes:
    return plistlib.dumps(
        {
            "CFBundleIdentifier": identifier,
            "CFBundleExecutable": executable,
            "CFBundlePackageType": package_type,
            "CFBundleVersion": version,
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def source_files() -> dict[str, bytes]:
    return {
        "Payload/App.app/Info.plist": info("com.example.app", "App", "APPL"),
        "Payload/App.app/App": b"MACHO:source-root",
        "Payload/App.app/embedded.mobileprovision": b"source-root-profile",
        "Payload/App.app/_CodeSignature/CodeResources": b"source-root-seal",
        "Payload/App.app/Assets.car": b"protected-assets",
        "Payload/App.app/PlugIns/Share.appex/Info.plist": info(
            "com.example.app.Share", "Share", "XPC!"
        ),
        "Payload/App.app/PlugIns/Share.appex/Share": b"MACHO:source-share",
        "Payload/App.app/PlugIns/Share.appex/embedded.mobileprovision": b"source-share-profile",
        "Payload/App.app/PlugIns/Share.appex/_CodeSignature/CodeResources": b"source-share-seal",
        "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.dylib": b"MACHO:source-kit",
        "Payload/App.app/PlugIns/Share.appex/config.json": b'{"protected":true}',
    }


def output_files() -> dict[str, bytes]:
    files = source_files()
    files.update(
        {
            "Payload/App.app/Info.plist": info("io.example.app", "App", "APPL"),
            "Payload/App.app/App": b"MACHO:signed-root",
            "Payload/App.app/embedded.mobileprovision": b"target-root-profile",
            "Payload/App.app/_CodeSignature/CodeResources": b"target-root-seal",
            "Payload/App.app/PlugIns/Share.appex/Info.plist": info(
                "io.example.app.Share", "Share", "XPC!"
            ),
            "Payload/App.app/PlugIns/Share.appex/Share": b"MACHO:signed-share",
            "Payload/App.app/PlugIns/Share.appex/embedded.mobileprovision": b"target-share-profile",
            "Payload/App.app/PlugIns/Share.appex/_CodeSignature/CodeResources": b"target-share-seal",
            "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.dylib": b"MACHO:signed-kit",
        }
    )
    return files


def archive(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as output:
        for name, content in sorted(files.items()):
            output.writestr(name, content)
    return path


def node(
    path: str,
    executable: str,
    kind: BundleNodeKind,
    order: int,
    target_bundle_id: str | None,
) -> SigningNodePlan:
    empty = normalize_entitlements({})
    return SigningNodePlan(
        PurePosixPath(path),
        PurePosixPath(executable),
        kind,
        order,
        target_bundle_id,
        f"PROFILE_{order}" if target_bundle_id is not None else None,
        (
            PurePosixPath(f"profiles/{order}.mobileprovision")
            if target_bundle_id is not None
            else None
        ),
        str(order) * 64 if target_bundle_id is not None else None,
        empty.values,
        empty.sha256,
    )


def plan(source_ipa: Path) -> SigningPlan:
    nodes = (
        node(
            "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.dylib",
            "Payload/App.app/PlugIns/Share.appex/Frameworks/Kit.dylib",
            BundleNodeKind.DYLIB,
            0,
            None,
        ),
        node(
            "Payload/App.app/PlugIns/Share.appex",
            "Payload/App.app/PlugIns/Share.appex/Share",
            BundleNodeKind.APP_EXTENSION,
            1,
            "io.example.app.Share",
        ),
        node(
            "Payload/App.app",
            "Payload/App.app/App",
            BundleNodeKind.APP,
            2,
            "io.example.app",
        ),
    )
    return SigningPlan(
        "Fixture",
        hashlib.sha256(source_ipa.read_bytes()).hexdigest(),
        "a" * 64,
        "b" * 64,
        SigningBackendIdentity("fixture", "1", "c" * 64, "1"),
        nodes,
        "d" * 64,
    )


def verify(tmp_path: Path, files: dict[str, bytes]) -> tuple[VerificationFinding, ...]:
    source = archive(tmp_path / "source.ipa", source_files())
    output = archive(tmp_path / "output.ipa", files)
    return verify_output_integrity(plan(source), source, output, macho_probe=MarkerMachOProbe())


def finding(findings: tuple[VerificationFinding, ...], check: str) -> VerificationFinding:
    return next(item for item in findings if item.check == check)


def test_accepts_only_planned_signing_mutations(tmp_path: Path) -> None:
    findings = verify(tmp_path, output_files())

    assert [item.check for item in findings] == [
        "source-artifact",
        "safe-output-archive",
        "source-plan-node-set",
        "output-graph-parity",
        "planned-identifiers",
        "executable-set",
        "protected-info-plists",
        "protected-payload",
    ]
    assert all(item.passed for item in findings)


def test_rejects_identifier_and_non_identifier_info_drift(tmp_path: Path) -> None:
    identifier_drift = output_files()
    identifier_drift["Payload/App.app/Info.plist"] = info("io.wrong", "App", "APPL")
    identifier_findings = verify(tmp_path, identifier_drift)
    assert not finding(identifier_findings, "planned-identifiers").passed
    assert finding(identifier_findings, "protected-info-plists").passed

    version_drift = output_files()
    version_drift["Payload/App.app/Info.plist"] = info("io.example.app", "App", "APPL", version="2")
    version_findings = verify(tmp_path, version_drift)
    assert not finding(version_findings, "protected-info-plists").passed


def test_rejects_added_executable_and_protected_payload_drift(tmp_path: Path) -> None:
    extra = output_files()
    extra["Payload/App.app/Helpers/Injected"] = b"MACHO:injected"
    extra_findings = verify(tmp_path, extra)
    assert not finding(extra_findings, "output-graph-parity").passed
    assert not finding(extra_findings, "executable-set").passed
    assert not finding(extra_findings, "protected-payload").passed

    changed = output_files()
    changed["Payload/App.app/Assets.car"] = b"changed"
    changed_findings = verify(tmp_path, changed)
    assert not finding(changed_findings, "protected-payload").passed


def test_rejects_wrong_source_and_malformed_output_archive(tmp_path: Path) -> None:
    source = archive(tmp_path / "source.ipa", source_files())
    output = archive(tmp_path / "output.ipa", output_files())
    wrong_plan = plan(source)
    wrong_plan = SigningPlan(
        wrong_plan.task_name,
        "0" * 64,
        wrong_plan.graph_sha256,
        wrong_plan.certificate_sha256,
        wrong_plan.backend,
        wrong_plan.nodes,
        wrong_plan.plan_sha256,
    )
    findings = verify_output_integrity(wrong_plan, source, output, macho_probe=MarkerMachOProbe())
    assert not finding(findings, "source-artifact").passed

    malformed = tmp_path / "malformed.ipa"
    malformed.write_bytes(b"not a zip")
    with pytest.raises(DomainError) as caught:
        verify_output_integrity(wrong_plan, source, malformed, macho_probe=MarkerMachOProbe())
    assert caught.value.code is ErrorCode.ARCHIVE_INVALID
