"""ZIP preflight and extraction without trusting archive member paths."""

from __future__ import annotations

import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sideloadedipa.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_entries: int = 50_000
    max_entry_uncompressed_bytes: int = 1024 * 1024 * 1024
    max_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_compression_ratio: float = 1_000


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    index: int
    path: PurePosixPath
    is_directory: bool
    size: int
    compressed_size: int
    mode: int


def _archive_error(
    code: ErrorCode,
    message: str,
    *,
    path: str | None = None,
    details: tuple[tuple[str, str | int | float], ...] = (),
) -> DomainError:
    context = (("path", path), *details) if path is not None else details
    return DomainError(
        code,
        message,
        remediation="use a trusted IPA whose archive passes all extraction limits",
        safe_details=context,
    )


def _normalized_path(info: zipfile.ZipInfo) -> PurePosixPath:
    original = info.orig_filename
    if "\x00" in original:
        raise _archive_error(
            ErrorCode.ARCHIVE_PATH_INVALID,
            "archive member path contains NUL",
            path=original.split("\x00", 1)[0],
        )
    portable = original.replace("\\", "/")
    if (
        portable.startswith("/")
        or portable.startswith("//")
        or (len(portable) >= 2 and portable[0].isalpha() and portable[1] == ":")
    ):
        raise _archive_error(
            ErrorCode.ARCHIVE_PATH_INVALID,
            "archive member path is absolute",
            path=portable,
        )
    raw_parts = portable.split("/")
    if ".." in raw_parts:
        raise _archive_error(
            ErrorCode.ARCHIVE_PATH_INVALID,
            "archive member path traverses its extraction root",
            path=portable,
        )
    path = PurePosixPath(portable)
    if not path.parts or str(path) in {"", "."}:
        raise _archive_error(
            ErrorCode.ARCHIVE_PATH_INVALID,
            "archive member path is empty",
            path=portable,
        )
    return path


def _unix_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF if info.create_system == 3 else 0


def _validate_file_type(info: zipfile.ZipInfo, path: PurePosixPath) -> tuple[bool, int]:
    mode = _unix_mode(info)
    file_type = stat.S_IFMT(mode)
    is_directory = info.is_dir()
    allowed_type = file_type in {
        0,
        stat.S_IFDIR if is_directory else stat.S_IFREG,
    }
    if not allowed_type:
        raise _archive_error(
            ErrorCode.ARCHIVE_SPECIAL_FILE,
            "archive member is a link or special file",
            path=str(path),
        )
    return is_directory, mode


def validate_archive_entries(
    infos: Sequence[zipfile.ZipInfo], limits: ArchiveLimits = ArchiveLimits()
) -> tuple[ArchiveEntry, ...]:
    """Validate every central-directory entry before extraction begins."""

    if len(infos) > limits.max_entries:
        raise _archive_error(
            ErrorCode.ARCHIVE_LIMIT_EXCEEDED,
            "archive contains too many entries",
            details=(("entries", len(infos)), ("limit", limits.max_entries)),
        )

    entries: list[ArchiveEntry] = []
    normalized_paths: dict[str, str] = {}
    total_size = 0
    for index, info in enumerate(infos):
        path = _normalized_path(info)
        duplicate_key = unicodedata.normalize("NFC", str(path)).casefold()
        previous = normalized_paths.get(duplicate_key)
        if previous is not None:
            raise _archive_error(
                ErrorCode.ARCHIVE_PATH_DUPLICATE,
                "archive contains duplicate normalized paths",
                path=str(path),
                details=(("first_path", previous),),
            )
        normalized_paths[duplicate_key] = str(path)

        is_directory, mode = _validate_file_type(info, path)
        if info.file_size > limits.max_entry_uncompressed_bytes:
            raise _archive_error(
                ErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                "archive member expanded size exceeds the configured limit",
                path=str(path),
                details=(
                    ("expanded_bytes", info.file_size),
                    ("limit", limits.max_entry_uncompressed_bytes),
                ),
            )
        total_size += info.file_size
        if total_size > limits.max_uncompressed_bytes:
            raise _archive_error(
                ErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                "archive expanded size exceeds the configured limit",
                path=str(path),
                details=(
                    ("expanded_bytes", total_size),
                    ("limit", limits.max_uncompressed_bytes),
                ),
            )
        if info.file_size:
            ratio = info.file_size / info.compress_size if info.compress_size else float("inf")
            if ratio > limits.max_compression_ratio:
                raise _archive_error(
                    ErrorCode.ARCHIVE_LIMIT_EXCEEDED,
                    "archive member compression ratio exceeds the configured limit",
                    path=str(path),
                    details=(("ratio", ratio), ("limit", limits.max_compression_ratio)),
                )
        entries.append(
            ArchiveEntry(
                index=index,
                path=path,
                is_directory=is_directory,
                size=info.file_size,
                compressed_size=info.compress_size,
                mode=mode,
            )
        )
    return tuple(entries)


def extract_ipa_safely(
    ipa_path: Path,
    destination: Path,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
) -> tuple[ArchiveEntry, ...]:
    """Preflight an IPA and extract only validated regular files and directories."""

    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise _archive_error(
            ErrorCode.WORKSPACE_INVALID,
            "archive extraction destination must be empty",
            path=destination.name,
        )
    try:
        with zipfile.ZipFile(ipa_path) as archive:
            infos = archive.infolist()
            entries = validate_archive_entries(infos, limits)
            destination.mkdir(parents=True, exist_ok=True)
            destination_root = destination.resolve()
            for entry in entries:
                target = destination / Path(*entry.path.parts)
                if not target.resolve().is_relative_to(destination_root):
                    raise _archive_error(
                        ErrorCode.ARCHIVE_PATH_INVALID,
                        "archive member resolves outside its extraction root",
                        path=str(entry.path),
                    )
                if entry.is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(entry.mode & 0o777 or 0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(infos[entry.index]) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(entry.mode & 0o777 or 0o644)
            return entries
    except DomainError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise _archive_error(
            ErrorCode.ARCHIVE_INVALID,
            "IPA archive could not be read or extracted",
            path=ipa_path.name,
        ) from error
