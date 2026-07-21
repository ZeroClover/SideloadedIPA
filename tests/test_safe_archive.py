"""Tests for safe all-before-write IPA extraction."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from sideloadedipa.errors import DomainError, ErrorCode
from sideloadedipa.ipa import ArchiveLimits, extract_ipa_safely, validate_archive_entries


def info(name: str, mode: int = stat.S_IFREG | 0o644) -> zipfile.ZipInfo:
    value = zipfile.ZipInfo(name)
    value.create_system = 3
    value.external_attr = mode << 16
    return value


def write_archive(path: Path, members: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in members:
            archive.writestr(member, content)


def test_preflights_then_extracts_regular_files_with_modes(tmp_path: Path) -> None:
    ipa = tmp_path / "safe.ipa"
    directory = info("Payload/App.app/", stat.S_IFDIR | 0o755)
    executable = info("Payload/App.app/App", stat.S_IFREG | 0o755)
    plist = info("Payload/App.app/Info.plist")
    write_archive(ipa, [(directory, b""), (executable, b"binary"), (plist, b"plist")])

    destination = tmp_path / "extract"
    entries = extract_ipa_safely(ipa, destination)

    assert [str(entry.path) for entry in entries] == [
        "Payload/App.app",
        "Payload/App.app/App",
        "Payload/App.app/Info.plist",
    ]
    assert (destination / "Payload/App.app/App").read_bytes() == b"binary"
    assert stat.S_IMODE((destination / "Payload/App.app/App").stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "name",
    [
        "/absolute",
        "C:\\absolute",
        "\\\\server\\share",
        "Payload/../escape",
    ],
)
def test_rejects_absolute_and_traversal_paths(name: str) -> None:
    with pytest.raises(DomainError) as caught:
        validate_archive_entries([info(name)])

    assert caught.value.code is ErrorCode.ARCHIVE_PATH_INVALID


def test_rejects_nul_using_original_filename() -> None:
    member = info("Payload/App.app/file")
    member.orig_filename = "Payload/App.app/file\x00hidden"

    with pytest.raises(DomainError) as caught:
        validate_archive_entries([member])

    assert caught.value.code is ErrorCode.ARCHIVE_PATH_INVALID


def test_rejects_duplicate_normalized_paths() -> None:
    with pytest.raises(DomainError) as caught:
        validate_archive_entries(
            [
                info("Payload/App.app/File"),
                info("Payload//App.app/file"),
            ]
        )

    assert caught.value.code is ErrorCode.ARCHIVE_PATH_DUPLICATE


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK])
def test_rejects_links_and_special_files(file_type: int) -> None:
    with pytest.raises(DomainError) as caught:
        validate_archive_entries([info("Payload/special", file_type | 0o755)])

    assert caught.value.code is ErrorCode.ARCHIVE_SPECIAL_FILE


def test_rejects_conflicting_directory_metadata() -> None:
    with pytest.raises(DomainError) as caught:
        validate_archive_entries([info("Payload/", stat.S_IFREG | 0o755)])

    assert caught.value.code is ErrorCode.ARCHIVE_SPECIAL_FILE


def test_rejects_entry_count_expanded_size_and_compression_ratio() -> None:
    first = info("one")
    first.file_size = 6
    first.compress_size = 3
    second = info("two")
    second.file_size = 6
    second.compress_size = 3

    with pytest.raises(DomainError) as count:
        validate_archive_entries([first, second], ArchiveLimits(max_entries=1))
    assert count.value.code is ErrorCode.ARCHIVE_LIMIT_EXCEEDED

    with pytest.raises(DomainError) as expanded:
        validate_archive_entries([first, second], ArchiveLimits(max_uncompressed_bytes=10))
    assert expanded.value.code is ErrorCode.ARCHIVE_LIMIT_EXCEEDED

    first.compress_size = 0
    with pytest.raises(DomainError) as ratio:
        validate_archive_entries([first], ArchiveLimits(max_compression_ratio=2))
    assert ratio.value.code is ErrorCode.ARCHIVE_LIMIT_EXCEEDED


def test_preflight_failure_writes_nothing(tmp_path: Path) -> None:
    ipa = tmp_path / "unsafe.ipa"
    write_archive(
        ipa,
        [
            (info("Payload/App.app/Info.plist"), b"valid first"),
            (info("../escape"), b"invalid second"),
        ],
    )
    destination = tmp_path / "extract"

    with pytest.raises(DomainError):
        extract_ipa_safely(ipa, destination)

    assert not destination.exists()
    assert not (tmp_path / "escape").exists()


def test_rejects_bad_zip_and_non_empty_destination(tmp_path: Path) -> None:
    bad = tmp_path / "bad.ipa"
    bad.write_bytes(b"not zip")
    with pytest.raises(DomainError) as invalid:
        extract_ipa_safely(bad, tmp_path / "bad-output")
    assert invalid.value.code is ErrorCode.ARCHIVE_INVALID

    safe = tmp_path / "safe.ipa"
    write_archive(safe, [(info("file"), b"data")])
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "existing").write_text("keep")
    with pytest.raises(DomainError) as occupied:
        extract_ipa_safely(safe, destination)
    assert occupied.value.code is ErrorCode.WORKSPACE_INVALID

    file_destination = tmp_path / "file-destination"
    file_destination.write_text("keep")
    with pytest.raises(DomainError) as file_error:
        extract_ipa_safely(safe, file_destination)
    assert file_error.value.code is ErrorCode.WORKSPACE_INVALID
