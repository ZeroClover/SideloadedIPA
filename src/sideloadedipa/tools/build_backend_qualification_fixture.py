"""Build the deterministic four-bundle IPA used by backend qualification."""

from __future__ import annotations

import hashlib
import plistlib
import zipfile
from pathlib import Path
from typing import Any

SOURCE_EXECUTABLES = {
    "root": "Payload/LiveContainer.app/LiveContainer",
    "process": "Payload/LiveContainer.app/PlugIns/LiveProcess.appex/LiveProcess",
    "launch": "Payload/LiveContainer.app/PlugIns/LaunchAppExtension.appex/LaunchAppExtension",
    "share": "Payload/LiveContainer.app/PlugIns/ShareExtension.appex/ShareExtension",
}
TARGETS = {
    "root": (
        "Payload/Qualification.app",
        "Qualification",
        "io.zeroclover.app.livecontainer",
        "APPL",
    ),
    "process": (
        "Payload/Qualification.app/PlugIns/LiveProcess.appex",
        "LiveProcess",
        "io.zeroclover.app.livecontainer.LiveProcess",
        "XPC!",
    ),
    "launch": (
        "Payload/Qualification.app/PlugIns/LaunchAppExtension.appex",
        "LaunchAppExtension",
        "io.zeroclover.app.livecontainer.LaunchAppExtension",
        "XPC!",
    ),
    "share": (
        "Payload/Qualification.app/PlugIns/ShareExtension.appex",
        "ShareExtension",
        "io.zeroclover.app.livecontainer.ShareExtension",
        "XPC!",
    ),
}
FIXED_ZIP_TIME = (2026, 7, 21, 0, 0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def info_plist(executable: str, identifier: str, package_type: str) -> bytes:
    document: dict[str, Any] = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": executable,
        "CFBundleIdentifier": identifier,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": executable,
        "CFBundlePackageType": package_type,
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "MinimumOSVersion": "15.0",
    }
    if package_type == "XPC!":
        document["NSExtension"] = {"NSExtensionPointIdentifier": "com.apple.backend-qualification"}
    return plistlib.dumps(document, fmt=plistlib.FMT_BINARY, sort_keys=True)


def zip_info(name: str, executable: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    return info


def build_fixture(source_ipa: Path, output_ipa: Path, expected_source_sha256: str) -> None:
    actual_sha256 = sha256_file(source_ipa)
    if actual_sha256 != expected_source_sha256:
        raise ValueError(
            f"source IPA SHA-256 is {actual_sha256}, expected {expected_source_sha256}"
        )

    output_ipa.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(source_ipa) as source,
        zipfile.ZipFile(
            output_ipa, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as output,
    ):
        source_names = set(source.namelist())
        missing = sorted(set(SOURCE_EXECUTABLES.values()) - source_names)
        if missing:
            raise ValueError(f"source IPA is missing fixture executables: {missing}")

        for role in TARGETS:
            bundle_path, executable, identifier, package_type = TARGETS[role]
            output.writestr(
                zip_info(f"{bundle_path}/Info.plist", executable=False),
                info_plist(executable, identifier, package_type),
            )
            output.writestr(
                zip_info(f"{bundle_path}/{executable}", executable=True),
                source.read(SOURCE_EXECUTABLES[role]),
            )
