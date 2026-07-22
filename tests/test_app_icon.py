"""Tests for scripts/app_icon.py - icon resolution, format detection, normalisation."""

import io
import plistlib
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from PIL import Image

from sideloadedipa.adapters.publication.icons import (
    ICON_SIZE,
    IPA_SCHEME,
    IconError,
    build_icon_png,
    decode_cgbi,
    extract_icon_from_ipa,
    normalize_to_png,
    resolve_icon_url,
    sniff_format,
)

REPO = "https://github.com/owner/repo"


# ── helpers ──────────────────────────────────────────────────────────────


def _png(
    size: tuple[int, int] = (64, 64), color: tuple[int, int, int, int] = (10, 20, 30, 255)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _webp(size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (1, 2, 3, 255)).save(buf, format="WEBP")
    return buf.getvalue()


def _cgbi_png(
    width: int = 4, height: int = 4, rgba: tuple[int, int, int, int] = (200, 100, 50, 255)
) -> bytes:
    """Build an Apple CgBI PNG: BGRA channel order, premultiplied, headerless deflate."""
    r, g, b, a = rgba
    pr, pg, pb = (r * a + 127) // 255, (g * a + 127) // 255, (b * a + 127) // 255
    raw = b"".join(b"\x00" + bytes([pb, pg, pr, a]) * width for _ in range(height))
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    idat = comp.compress(raw) + comp.flush()

    def chunk(ctype: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + ctype
            + body
            + struct.pack(">I", zlib.crc32(ctype + body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"CgBI", b"\x50\x00\x20\x06")
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def _ipa(tmp_path: Path, app: str, icon_keys: dict, members: dict[str, bytes]) -> Path:
    path = tmp_path / f"{app}.ipa"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"Payload/{app}.app/Info.plist", plistlib.dumps(icon_keys))
        for name, body in members.items():
            zf.writestr(f"Payload/{app}.app/{name}", body)
    return path


# ── source resolution ────────────────────────────────────────────────────


class TestResolveIconUrl:
    """icon_path may be an absolute URL or a path inside the task's repo."""

    def test_absolute_url_passes_through(self) -> None:
        url = "https://cdn.example.com/icon.png"
        assert resolve_icon_url(url, REPO, "v1.0") == url

    def test_repo_path_resolves_against_ref(self) -> None:
        url = resolve_icon_url("ios/Runner/Icon.png", REPO, "v2.1.0")
        assert url == "https://raw.githubusercontent.com/owner/repo/v2.1.0/ios/Runner/Icon.png"

    def test_ref_defaults_to_head(self) -> None:
        assert resolve_icon_url("a/b.png", REPO, None).endswith("/repo/HEAD/a/b.png")

    def test_special_characters_are_quoted(self) -> None:
        """Upstream filenames contain spaces and @ — both must survive."""
        url = resolve_icon_url("assets/Stik New-1024@1x.png", REPO, "v1")
        assert url.endswith("/assets/Stik%20New-1024%401x.png")

    def test_leading_slash_is_tolerated(self) -> None:
        assert resolve_icon_url("/a/b.png", REPO, "v1").endswith("/repo/v1/a/b.png")

    def test_repo_relative_without_repo_url_raises(self) -> None:
        with pytest.raises(IconError, match="no repo_url"):
            resolve_icon_url("ios/Icon.png", None, "v1")


# ── format detection ─────────────────────────────────────────────────────


class TestSniffFormat:
    """Format comes from magic bytes; upstream commits WebP data named .png."""

    def test_png(self) -> None:
        assert sniff_format(_png()) == "png"

    def test_cgbi_distinguished_from_png(self) -> None:
        assert sniff_format(_cgbi_png()) == "cgbi"

    def test_webp(self) -> None:
        assert sniff_format(_webp()) == "webp"

    def test_jpeg(self) -> None:
        assert sniff_format(b"\xff\xd8\xff\xe0" + b"\x00" * 32) == "jpeg"

    def test_gif(self) -> None:
        assert sniff_format(b"GIF89a" + b"\x00" * 32) == "gif"

    def test_svg(self) -> None:
        assert sniff_format(b'<?xml version="1.0"?><svg xmlns="...">') == "svg"

    def test_bare_svg_tag(self) -> None:
        assert sniff_format(b"  <svg viewBox='0 0 1 1'></svg>") == "svg"

    def test_unknown(self) -> None:
        assert sniff_format(b"not an image at all") == "unknown"


# ── CgBI decoding ────────────────────────────────────────────────────────


class TestDecodeCgbi:
    """Apple's PNG variant: BGRA order, premultiplied alpha, raw DEFLATE."""

    def test_channels_are_reordered_to_rgba(self) -> None:
        width, height, rgba = decode_cgbi(_cgbi_png(rgba=(200, 100, 50, 255)))
        assert (width, height) == (4, 4)
        assert tuple(rgba[:4]) == (200, 100, 50, 255)

    def test_alpha_is_unpremultiplied(self) -> None:
        _, _, rgba = decode_cgbi(_cgbi_png(rgba=(200, 100, 50, 128)))
        r, g, b, a = rgba[0], rgba[1], rgba[2], rgba[3]
        assert a == 128
        # Round-tripping through premultiplication is lossy; allow a small delta.
        assert abs(r - 200) <= 2 and abs(g - 100) <= 2 and abs(b - 50) <= 2

    def test_malformed_raises(self) -> None:
        with pytest.raises(IconError):
            decode_cgbi(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


# ── normalisation ────────────────────────────────────────────────────────


class TestNormalizeToPng:
    """Everything becomes a square PNG of at most ICON_SIZE px."""

    def test_downscales_oversized_source(self) -> None:
        img = Image.open(io.BytesIO(normalize_to_png(_png((1024, 1024)))))
        assert img.size == (ICON_SIZE, ICON_SIZE)
        assert img.format == "PNG"

    def test_does_not_upscale_small_source(self) -> None:
        """A 152x152 IPA icon must not be inflated to 512."""
        assert Image.open(io.BytesIO(normalize_to_png(_png((152, 152))))).size == (152, 152)

    def test_webp_is_converted_to_png(self) -> None:
        img = Image.open(io.BytesIO(normalize_to_png(_webp((256, 256)))))
        assert img.format == "PNG"

    def test_cgbi_is_converted_to_png(self) -> None:
        img = Image.open(io.BytesIO(normalize_to_png(_cgbi_png(8, 8))))
        assert img.format == "PNG" and img.size == (8, 8)

    def test_sixteen_bit_is_reduced_to_eight(self) -> None:
        buf = io.BytesIO()
        Image.new("I;16", (64, 64), 4096).convert("RGBA").save(buf, format="PNG")
        img = Image.open(io.BytesIO(normalize_to_png(buf.getvalue())))
        assert img.mode == "RGBA"

    def test_non_square_is_padded_not_stretched(self) -> None:
        img = Image.open(io.BytesIO(normalize_to_png(_png((200, 100))))).convert("RGBA")
        assert img.width == img.height == 200
        # Padding is transparent; the source colour stays centred.
        assert img.getpixel((100, 100))[3] == 255
        assert img.getpixel((100, 5))[3] == 0

    def test_opaque_corners_are_preserved(self) -> None:
        """The page rounds corners in CSS, so normalisation must not do it."""
        img = Image.open(io.BytesIO(normalize_to_png(_png((512, 512))))).convert("RGBA")
        assert img.getpixel((0, 0))[3] == 255
        assert img.getpixel((511, 511))[3] == 255

    def test_svg_raises(self) -> None:
        with pytest.raises(IconError, match="SVG"):
            normalize_to_png(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(IconError, match="Unrecognised"):
            normalize_to_png(b"definitely not an image")


# ── extraction from a signed IPA ─────────────────────────────────────────


class TestExtractIconFromIpa:
    """Icon basenames come from Info.plist, not from an AppIcon* glob."""

    def test_uses_declared_basename_not_appicon_glob(self, tmp_path: Path) -> None:
        """Xcode names the files after the asset-catalog set, e.g. StikDebug60x60."""
        ipa = _ipa(
            tmp_path,
            "StikDebug",
            {"CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["StikDebug60x60"]}}},
            {"StikDebug60x60@2x.png": _png((120, 120)), "unrelated.png": _png((999, 999))},
        )
        assert struct.unpack(">II", extract_icon_from_ipa(ipa)[16:24]) == (120, 120)

    def test_picks_largest_across_idiom_keys(self, tmp_path: Path) -> None:
        """The 76pt icon is often declared only under CFBundleIcons~ipad."""
        ipa = _ipa(
            tmp_path,
            "StikDebug",
            {
                "CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["StikDebug60x60"]}},
                "CFBundleIcons~ipad": {
                    "CFBundlePrimaryIcon": {
                        "CFBundleIconFiles": ["StikDebug60x60", "StikDebug76x76"]
                    }
                },
            },
            {
                "StikDebug60x60@2x.png": _png((120, 120)),
                "StikDebug76x76@2x~ipad.png": _png((152, 152)),
            },
        )
        assert struct.unpack(">II", extract_icon_from_ipa(ipa)[16:24]) == (152, 152)

    def test_ignores_nested_resources(self, tmp_path: Path) -> None:
        """Only the bundle root counts; framework assets must not win on size."""
        ipa = _ipa(
            tmp_path,
            "App",
            {"CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}},
            {
                "AppIcon60x60@2x.png": _png((120, 120)),
                "Frameworks/X.bundle/AppIcon60x60@3x.png": _png((512, 512)),
            },
        )
        assert struct.unpack(">II", extract_icon_from_ipa(ipa)[16:24]) == (120, 120)

    def test_missing_icon_files_key_raises(self, tmp_path: Path) -> None:
        ipa = _ipa(tmp_path, "App", {"CFBundleName": "App"}, {})
        with pytest.raises(IconError, match="no CFBundleIconFiles"):
            extract_icon_from_ipa(ipa)

    def test_declared_but_absent_icon_raises(self, tmp_path: Path) -> None:
        ipa = _ipa(
            tmp_path,
            "App",
            {"CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}},
            {},
        )
        with pytest.raises(IconError, match="No icon PNG"):
            extract_icon_from_ipa(ipa)

    def test_missing_plist_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.ipa"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Payload/readme.txt", "nothing here")
        with pytest.raises(IconError, match="Info.plist"):
            extract_icon_from_ipa(path)


# ── orchestration ────────────────────────────────────────────────────────


class TestBuildIconPng:
    """build_icon_png dispatches on the icon_path form."""

    def test_ipa_scheme_reads_the_signed_ipa(self, tmp_path: Path) -> None:
        ipa = _ipa(
            tmp_path,
            "App",
            {"CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}},
            {"AppIcon60x60@2x.png": _cgbi_png(120, 120)},
        )
        img = Image.open(io.BytesIO(build_icon_png(IPA_SCHEME, None, ipa_path=ipa)))
        assert img.format == "PNG" and img.size == (120, 120)

    def test_ipa_scheme_without_ipa_raises(self) -> None:
        with pytest.raises(IconError, match="no signed IPA"):
            build_icon_png(IPA_SCHEME, None, ipa_path=None)

    def test_url_source_is_fetched_and_normalised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "sideloadedipa.adapters.publication.icons.fetch_bytes",
            lambda url, **kw: _webp((1024, 1024)),
        )
        img = Image.open(io.BytesIO(build_icon_png("https://example.com/i.png", None)))
        assert img.format == "PNG" and img.size == (ICON_SIZE, ICON_SIZE)
