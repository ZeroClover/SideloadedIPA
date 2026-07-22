"""Metadata read from the root application in an IPA."""

from __future__ import annotations

import plistlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sideloadedipa.errors import DomainError, ErrorCode

_ROOT_INFO_PATTERN = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")


@dataclass(frozen=True, slots=True)
class IpaMetadata:
    bundle_id: str
    version: str


def read_ipa_metadata(path: Path) -> IpaMetadata:
    """Read the authoritative bundle identifier and version from the root app."""

    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if _ROOT_INFO_PATTERN.fullmatch(name)]
            if len(names) != 1:
                raise ValueError("IPA must contain exactly one root application Info.plist")
            document = plistlib.loads(archive.read(names[0]))
    except (
        OSError,
        ValueError,
        KeyError,
        plistlib.InvalidFileException,
        zipfile.BadZipFile,
    ) as error:
        raise DomainError(
            ErrorCode.SIGNING_VERIFICATION_FAILED,
            "verified IPA metadata could not be read",
            remediation="retain the previous publication and inspect the signed IPA report",
        ) from error

    bundle_id = document.get("CFBundleIdentifier") if isinstance(document, dict) else None
    version = (
        document.get("CFBundleShortVersionString") or document.get("CFBundleVersion")
        if isinstance(document, dict)
        else None
    )
    if (
        not isinstance(bundle_id, str)
        or not bundle_id
        or not isinstance(version, str)
        or not version
    ):
        raise DomainError(
            ErrorCode.SIGNING_VERIFICATION_FAILED,
            "verified IPA is missing root bundle identifier or version metadata",
            remediation="retain the previous publication and inspect the root Info.plist",
        )
    return IpaMetadata(bundle_id, version)
