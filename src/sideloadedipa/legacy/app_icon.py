#!/usr/bin/env python3
"""Resolve, fetch and normalise an app's high-resolution icon.

The signed IPA is a poor icon source: Xcode only copies the 60pt/76pt @2x
variants to the bundle root (152x152 at best), and the 1024x1024 master never
ships inside the app at all. The upstream repository does have it — but there
is no convention to key off. Flutter projects keep it at
``ios/Runner/Assets.xcassets/AppIcon.appiconset/Icon-App-1024x1024@1x.png``,
native projects use their own names and directories, and Icon Composer
(iOS 26) projects may carry only a ``.icon`` bundle of SVG layers.

So nothing is inferred: every task points at its own asset via ``icon_path``,
which takes one of three forms:

    <path>      a path inside the task's repo_url, fetched at the release tag
                so the icon matches the published build
    https://…   any absolute URL
    ipa:        the signed IPA itself — for projects whose repository has no
                square master (Icon Composer ships SVG layers, and the PNGs
                such projects commit are pre-rounded exports). Tops out at
                152x152, so prefer a repository asset when one exists.

Whatever comes back is normalised to a square 8-bit PNG. The source format is
detected from magic bytes rather than the file extension — upstream really
does commit WebP data under a ``.png`` name (Lakr233/Asspp), and Xcode-
processed PNGs use Apple's CgBI variant, which no standard decoder accepts.
"""

from __future__ import annotations

import io
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Optional

from sideloadedipa.sources import github_repository_name

# Icons render in an 84px tile; 512 covers @3x (252px) with room to spare while
# staying far smaller than the 1024 masters (which run to several MB).
ICON_SIZE = 512

# Upstream assets are small; a slow mirror should not stall the pipeline.
FETCH_TIMEOUT = 30
MAX_ICON_BYTES = 32 * 1024 * 1024

RAW_HOST = "https://raw.githubusercontent.com"

# icon_path value selecting the signed IPA as the source.
IPA_SCHEME = "ipa:"


class IconError(Exception):
    """Raised when an icon cannot be fetched or decoded."""


# ── source resolution ────────────────────────────────────────────────────


def resolve_icon_url(icon_path: str, repo_url: Optional[str], ref: Optional[str]) -> str:
    """Turn a task's ``icon_path`` into a fetchable URL.

    A full HTTP(S) URL is used as-is. Anything else is treated as a path inside
    ``repo_url`` and resolved against ``ref`` — the release tag being published,
    so the icon tracks the same commit as the IPA. ``ref`` defaults to HEAD.
    """
    if icon_path.startswith(("http://", "https://")):
        return icon_path

    if not repo_url:
        raise IconError(
            f"icon_path '{icon_path}' is repo-relative but the task has no repo_url; "
            "use a full HTTP(S) URL instead"
        )

    owner, repo = github_repository_name(repo_url).split("/", 1)
    # Upstream filenames contain spaces ("StikDebug New-iOS-Default-...png"),
    # so quote each segment while leaving the separators intact.
    quoted = "/".join(urllib.parse.quote(seg) for seg in icon_path.lstrip("/").split("/"))
    return f"{RAW_HOST}/{owner}/{repo}/{ref or 'HEAD'}/{quoted}"


def fetch_bytes(url: str, timeout: int = FETCH_TIMEOUT) -> bytes:
    """Download an icon asset, refusing implausibly large responses."""
    req = urllib.request.Request(url, headers={"User-Agent": "SideloadedIPA-icon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data: bytes = resp.read(MAX_ICON_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise IconError(f"HTTP {e.code} fetching {url}") from e
    except (urllib.error.URLError, OSError) as e:
        raise IconError(f"Failed to fetch {url}: {e}") from e

    if not data:
        raise IconError(f"Empty response from {url}")
    if len(data) > MAX_ICON_BYTES:
        raise IconError(f"Icon at {url} exceeds {MAX_ICON_BYTES} bytes")
    return data


# ── extraction from a signed IPA ─────────────────────────────────────────


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG's IHDR, tolerating a leading CgBI chunk."""
    off = 8
    while off + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[off : off + 8])
        if ctype == b"IHDR":
            w, h = struct.unpack(">II", data[off + 8 : off + 16])
            return w, h
        off += 12 + length
    raise IconError("PNG has no IHDR chunk")


def extract_icon_from_ipa(ipa_path: Path) -> bytes:
    """Pull the largest primary app icon out of a signed IPA.

    Xcode copies the primary icon's @2x variants to the app bundle root for
    backwards compatibility and lists their basenames in Info.plist under
    ``CFBundleIcons.CFBundlePrimaryIcon.CFBundleIconFiles``. Those names are
    NOT always ``AppIcon*`` — Xcode uses the asset-catalog set name, so
    StikDebug ships ``StikDebug60x60@2x.png``. Hence the plist lookup rather
    than a glob. Full-resolution masters live only inside ``Assets.car``,
    which needs Apple's CoreUI to read, so 152x152 is the practical ceiling.
    """
    import plistlib
    import zipfile

    with zipfile.ZipFile(ipa_path) as zf:
        names = zf.namelist()
        plists = [n for n in names if re.match(r"^Payload/[^/]+\.app/Info\.plist$", n)]
        if not plists:
            raise IconError("No Payload/*.app/Info.plist in IPA")
        plist_name = min(plists, key=len)
        app_dir = plist_name.rsplit("/", 1)[0]
        info = plistlib.loads(zf.read(plist_name))

        # Idiom-specific variants live under their own keys, and the largest
        # icon is often only listed there: StikDebug declares 60pt under
        # CFBundleIcons but 60pt + 76pt under CFBundleIcons~ipad.
        basenames: list[str] = []
        for key, value in info.items():
            if not key.startswith("CFBundleIcons") or not isinstance(value, dict):
                continue
            primary = value.get("CFBundlePrimaryIcon")
            if not isinstance(primary, dict):
                continue
            for name in primary.get("CFBundleIconFiles") or []:
                if name not in basenames:
                    basenames.append(name)
        if not basenames:
            raise IconError("Info.plist declares no CFBundleIconFiles")

        best: Optional[tuple[int, str]] = None
        for member in names:
            if not member.startswith(f"{app_dir}/") or not member.lower().endswith(".png"):
                continue
            leaf = member[len(app_dir) + 1 :]
            if "/" in leaf:  # bundle root only — skip nested resources
                continue
            if not any(leaf.startswith(b) for b in basenames):
                continue
            try:
                width, _ = _png_dimensions(zf.read(member))
            except Exception:
                continue
            if best is None or width > best[0]:
                best = (width, member)

        if best is None:
            raise IconError(f"No icon PNG matching {basenames} at {app_dir}/")
        print(f"[info] Icon source: {best[1]} ({best[0]}x{best[0]})")
        return zf.read(best[1])


# ── format detection ─────────────────────────────────────────────────────


def sniff_format(data: bytes) -> str:
    """Identify an image by magic bytes. Returns a short format name.

    Extensions are not trustworthy here — see the module docstring.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        # Apple's pngcrush marks its variant with a CgBI chunk ahead of IHDR.
        return "cgbi" if data[12:16] == b"CgBI" else "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
        return "tiff"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "heif"
    stripped = data.lstrip()[:256].lower()
    if stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in stripped):
        return "svg"
    return "unknown"


# ── CgBI decoding ────────────────────────────────────────────────────────


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_cgbi(data: bytes) -> tuple[int, int, bytes]:
    """Decode an Apple CgBI PNG to (width, height, RGBA bytes).

    Xcode's pngcrush rewrites PNGs into a private variant: the zlib header is
    stripped from IDAT, channels are stored BGRA, and colour is premultiplied
    by alpha. Pillow cannot read these, so undo all three here.
    """
    idat = bytearray()
    width = height = 0
    off = 8
    while off + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[off : off + 8])
        body = data[off + 8 : off + 8 + length]
        if ctype == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body[:13])
            if depth != 8 or color != 6 or interlace != 0:
                raise IconError(
                    f"Unsupported CgBI PNG (depth={depth} color={color} interlace={interlace}); "
                    "only 8-bit RGBA non-interlaced is handled"
                )
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        off += 12 + length

    if not width or not height or not idat:
        raise IconError("Malformed CgBI PNG: missing IHDR or IDAT")

    # -15 window size = raw DEFLATE; Apple drops the two-byte zlib header.
    raw = zlib.decompressobj(-15).decompress(bytes(idat))

    bpp, stride = 4, width * 4
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            raise IconError("Truncated CgBI scanline data")
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        if len(line) < stride:
            raise IconError("Truncated CgBI scanline data")

        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        elif ftype != 0:
            raise IconError(f"Unknown PNG filter type {ftype}")

        out[y * stride : (y + 1) * stride] = line
        prev = line

    # BGRA -> RGBA, and reverse the alpha premultiplication.
    for i in range(0, len(out), 4):
        b, g, r, a = out[i], out[i + 1], out[i + 2], out[i + 3]
        if a and a != 255:
            r = min(255, (r * 255 + a // 2) // a)
            g = min(255, (g * 255 + a // 2) // a)
            b = min(255, (b * 255 + a // 2) // a)
        out[i], out[i + 1], out[i + 2] = r, g, b

    return width, height, bytes(out)


# ── normalisation ────────────────────────────────────────────────────────


def normalize_to_png(data: bytes, size: int = ICON_SIZE) -> bytes:
    """Convert any supported image to a square 8-bit RGBA PNG of ``size`` px.

    Corners are left square and fully opaque where the source is: the download
    page rounds icons with a CSS mask (``.icon-tile``), so an icon that already
    carries baked-in rounded corners would be visibly double-rounded.
    """
    from PIL import Image  # imported lazily so format sniffing stays dependency-free

    fmt = sniff_format(data)
    if fmt == "svg":
        raise IconError(
            "SVG icons are not supported; point icon_path at a raster asset "
            "(the .icon bundles shipped by Icon Composer are SVG layers)"
        )
    if fmt == "unknown":
        raise IconError("Unrecognised image format (not PNG/CgBI/WebP/JPEG/GIF/TIFF/HEIF)")

    if fmt == "cgbi":
        width, height, rgba = decode_cgbi(data)
        img = Image.frombytes("RGBA", (width, height), rgba)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as e:  # Pillow raises a wide range of decoder errors
            raise IconError(f"Failed to decode {fmt} image: {e}") from e

    # convert() also collapses 16-bit channels and palettes down to 8-bit.
    img = img.convert("RGBA")

    # App icons are square; pad rather than stretch if upstream ships otherwise.
    if img.width != img.height:
        side = max(img.width, img.height)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = canvas

    # Downscale only. Upscaling a 152x152 IPA-extracted icon to 512 would just
    # inflate the file without adding detail.
    if img.width > size:
        img = img.resize((size, size), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_icon_png(
    icon_path: str,
    repo_url: Optional[str],
    ref: Optional[str] = None,
    size: int = ICON_SIZE,
    ipa_path: Optional[Path] = None,
) -> bytes:
    """Resolve ``icon_path`` from any supported source; return a square PNG."""
    if icon_path.strip() == IPA_SCHEME:
        if ipa_path is None:
            raise IconError("icon_path is 'ipa:' but no signed IPA was supplied")
        data = extract_icon_from_ipa(ipa_path)
    else:
        url = resolve_icon_url(icon_path, repo_url, ref)
        print(f"[info] Fetching icon: {url}")
        data = fetch_bytes(url)

    png = normalize_to_png(data, size=size)
    out_w, out_h = _png_dimensions(png)
    print(f"[info] Icon normalised: {sniff_format(data)} -> PNG {out_w}x{out_h} ({len(png)} bytes)")
    return png


def main(argv: list[str]) -> int:
    """Ad-hoc conversion helper: ``app_icon.py <icon_path|url> [repo_url] [ref]``."""
    if not argv:
        print(__doc__)
        return 2
    icon_path = argv[0]
    repo_url = argv[1] if len(argv) > 1 else None
    ref = argv[2] if len(argv) > 2 else None
    try:
        png = build_icon_png(icon_path, repo_url, ref)
    except IconError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
