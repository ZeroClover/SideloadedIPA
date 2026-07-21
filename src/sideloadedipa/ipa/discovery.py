"""Discovery and validation of the root application bundle."""

from __future__ import annotations

import hashlib
import plistlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from sideloadedipa.domain import BundleNode, BundleNodeKind
from sideloadedipa.domain.identifiers import validate_bundle_identifier
from sideloadedipa.errors import DomainError, ErrorCode


def _inventory_error(
    code: ErrorCode,
    message: str,
    *,
    path: PurePosixPath | None = None,
    field: str | None = None,
    candidates: tuple[str, ...] | None = None,
) -> DomainError:
    details: list[tuple[str, str | tuple[str, ...]]] = []
    if path is not None:
        details.append(("path", str(path)))
    if field is not None:
        details.append(("field", field))
    if candidates is not None:
        details.append(("candidates", candidates))
    return DomainError(
        code,
        message,
        remediation="select a valid IPA containing one complete root application",
        safe_details=tuple(details),
    )


def _required_string(document: Mapping[str, object], field: str, bundle_path: PurePosixPath) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            f"{field} must be a non-empty string",
            path=bundle_path / "Info.plist",
            field=field,
        )
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _discover_profile_bundle(
    extracted_root: Path,
    relative_bundle: PurePosixPath,
    *,
    kind: BundleNodeKind,
    package_type: str,
    depth: int,
    parent_path: PurePosixPath | None,
    label: str,
) -> BundleNode:
    bundle = extracted_root / Path(*relative_bundle.parts)
    info_path = bundle / "Info.plist"
    if not info_path.is_file():
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            f"{label} Info.plist is missing",
            path=relative_bundle / "Info.plist",
            field="Info.plist",
        )
    try:
        raw_info = info_path.read_bytes()
        document = plistlib.loads(raw_info)
    except (OSError, plistlib.InvalidFileException) as error:
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            f"{label} Info.plist could not be decoded",
            path=relative_bundle / "Info.plist",
            field="Info.plist",
        ) from error
    if not isinstance(document, Mapping):
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            f"{label} Info.plist must contain a dictionary",
            path=relative_bundle / "Info.plist",
            field="Info.plist",
        )

    actual_package_type = _required_string(document, "CFBundlePackageType", relative_bundle)
    if actual_package_type != package_type:
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            f"{label} has an unsupported package type",
            path=relative_bundle / "Info.plist",
            field="CFBundlePackageType",
        )
    bundle_id = _required_string(document, "CFBundleIdentifier", relative_bundle)
    try:
        validate_bundle_identifier(bundle_id, field="CFBundleIdentifier")
    except DomainError as error:
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            f"{label} bundle identifier is invalid",
            path=relative_bundle / "Info.plist",
            field="CFBundleIdentifier",
        ) from error

    executable_name = _required_string(document, "CFBundleExecutable", relative_bundle)
    if Path(executable_name).name != executable_name or executable_name in {".", ".."}:
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            "CFBundleExecutable must be a file name",
            path=relative_bundle / "Info.plist",
            field="CFBundleExecutable",
        )
    executable = bundle / executable_name
    if not executable.is_file():
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            "root application executable is missing",
            path=relative_bundle / executable_name,
            field="CFBundleExecutable",
        )

    version = _required_string(document, "CFBundleVersion", relative_bundle)
    short_version_value = document.get("CFBundleShortVersionString")
    if short_version_value is not None and (
        not isinstance(short_version_value, str) or not short_version_value
    ):
        raise _inventory_error(
            ErrorCode.INVENTORY_METADATA_INVALID,
            "CFBundleShortVersionString must be a non-empty string when present",
            path=relative_bundle / "Info.plist",
            field="CFBundleShortVersionString",
        )

    return BundleNode(
        path=relative_bundle,
        kind=kind,
        depth=depth,
        executable_path=relative_bundle / executable_name,
        executable_sha256=_sha256(executable),
        parent_path=parent_path,
        source_bundle_id=bundle_id,
        info_plist_sha256=hashlib.sha256(raw_info).hexdigest(),
        version=version,
        short_version=short_version_value,
    )


def discover_root_app(extracted_root: Path) -> BundleNode:
    """Require and decode exactly one direct ``Payload/*.app`` bundle."""

    payload = extracted_root / "Payload"
    candidates = tuple(
        sorted(path for path in payload.glob("*.app") if path.is_dir() and path.parent == payload)
    )
    candidate_names = tuple(
        str(PurePosixPath("Payload") / candidate.name) for candidate in candidates
    )
    if len(candidates) != 1:
        raise _inventory_error(
            ErrorCode.INVENTORY_ROOT_AMBIGUOUS,
            "IPA must contain exactly one root application",
            candidates=candidate_names,
        )
    relative_bundle = PurePosixPath("Payload") / candidates[0].name
    return _discover_profile_bundle(
        extracted_root,
        relative_bundle,
        kind=BundleNodeKind.APP,
        package_type="APPL",
        depth=0,
        parent_path=None,
        label="root application",
    )
