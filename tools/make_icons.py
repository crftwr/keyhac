"""Render Keyhac's raster icon assets from the maintained SVG sources.

The vector artwork is the source of truth and lives in the repo, edited by
hand (Inkscape or a text editor), in the plain-SVG subset documented in
``tools/svgrender.py`` — a pure-stdlib rasterizer, so this script runs the
same on macOS and Windows with no image tooling installed:

- ``art/icon.svg`` — the color keycap (keyhac-win's app-icon design).
  Rendered here into every raster target.
- ``art/MenuExtraTemplate.svg`` — the macOS menu bar extra (line-art
  keycap, template alpha). Rendered here into the 1x/2x PNG pair; it is
  deliberately *not* loaded as SVG at runtime — macOS caches a
  system-side rasterization of vector status-item images by file
  identity, and an in-place edit of the SVG left menu bars compositing
  the stale raster of the old artwork (see the comment in the SVG).

Raster targets (all checked in; re-run only when the artwork changes):

- ``keyhac/ui/assets/keyhac.ico`` — Windows system tray *and* app icon.
  16/20/24/32/40/48 px as classic 32bpp BMP entries (the tray's small-icon
  metric across DPI scales, maximum shell compatibility), 64/128/256 px as
  PNG entries (Vista+) for Explorer's large views.
- ``keyhac/ui/assets/keyhac.icns`` — macOS app icon for the bundled app
  (doc/dev/packaging.md), PNG entries at every standard slot up to 1024.
- ``keyhac/ui/assets/MenuExtraTemplate.png`` + ``@2x`` — the menu bar
  extra at 19x18 pt (the 19x18 canvas at exact 1x/2x scale; puikit's
  tray loader pairs the @2x sibling and applies the AppKit "…Template"
  naming convention).

Adding a target (store banner, README art, …) is one line in ``build()``:
render the source SVG at the needed size and hand it to an encoder.

Usage: ``.venv/bin/python tools/make_icons.py [--check]``

``--check`` verifies the checked-in assets still match the SVG masters
(exit 1 on drift) instead of writing them — the guard for "edited the SVG,
forgot to regenerate". Containers are compared image-by-image with PNG
streams decompressed, not byte-for-byte: the pixels are deterministic (the
rasterizer is pure Python) but zlib output is not (Windows CPython bundles
zlib-ng, macOS links system zlib), so assets regenerated on the other OS
would false-fail a byte compare.
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

import svgrender

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "art"
ASSETS = ROOT / "keyhac" / "ui" / "assets"


# --- encoders ---------------------------------------------------------------

def png_bytes(pixels):
    h = len(pixels)
    w = len(pixels[0])
    raw = b"".join(
        b"\x00" + b"".join(bytes(px) for px in row) for row in pixels
    )

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def write_png(path, pixels):
    path.write_bytes(png_bytes(pixels))


def ico_bytes(bmp_images, png_images=()):
    """``bmp_images``: pixel grids written as classic 32bpp BMP entries (the
    doubled-height header + empty AND mask); ``png_images``: pixel grids
    written as PNG entries (valid from Vista on, and the only option at
    256 px). All squares."""
    entries = []
    for pixels in bmp_images:
        n = len(pixels)
        xor = b"".join(  # bottom-up BGRA rows
            bytes(c for px in row for c in (px[2], px[1], px[0], px[3]))
            for row in reversed(pixels)
        )
        and_stride = ((n + 31) // 32) * 4
        blob = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0,
                           len(xor), 0, 0, 0, 0) + xor + b"\x00" * (and_stride * n)
        entries.append((n, blob))
    for pixels in png_images:
        entries.append((len(pixels), png_bytes(pixels)))

    offset = 6 + 16 * len(entries)
    directory = struct.pack("<HHH", 0, 1, len(entries))
    for n, blob in entries:
        directory += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32,
                                 len(blob), offset)
        offset += len(blob)
    return directory + b"".join(blob for _n, blob in entries)


def write_ico(path, bmp_images, png_images=()):
    path.write_bytes(ico_bytes(bmp_images, png_images))


# icns entry types by (render px, type): the @2x slots (ic11-ic14) reuse the
# corresponding double-resolution render — same pixels, different point size.
_ICNS_SLOTS = (
    (16, b"icp4"), (32, b"icp5"), (64, b"icp6"),
    (128, b"ic07"), (256, b"ic08"), (512, b"ic09"), (1024, b"ic10"),
    (32, b"ic11"), (64, b"ic12"), (256, b"ic13"), (512, b"ic14"),
)


def icns_bytes(renders):
    """``renders``: {size_px: pixel grid} covering the sizes in _ICNS_SLOTS."""
    pngs = {size: png_bytes(pixels) for size, pixels in renders.items()}
    chunks = b"".join(
        icns_type + struct.pack(">I", 8 + len(pngs[size])) + pngs[size]
        for size, icns_type in _ICNS_SLOTS
    )
    return b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks


def write_icns(path, renders):
    path.write_bytes(icns_bytes(renders))


# --- asset comparison (--check) ---------------------------------------------

def _png_raw(data):
    """The decompressed scanline stream of a PNG this script wrote."""
    pos, idat = 8, b""
    while pos < len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        if data[pos + 4:pos + 8] == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    return zlib.decompress(idat)


def _blob_equal(a, b):
    sig = b"\x89PNG\r\n\x1a\n"
    if a[:8] == sig and b[:8] == sig:
        return _png_raw(a) == _png_raw(b)
    return a == b


def _ico_entries(data):
    count = struct.unpack_from("<HHH", data, 0)[2]
    sizes = [struct.unpack_from("<II", data, 6 + 16 * i + 8)
             for i in range(count)]
    return [data[offset:offset + size] for size, offset in sizes]


def _icns_entries(data):
    out, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack_from(">I", data, pos + 4)
        out.append(data[pos + 8:pos + length])
        pos += length
    return out


def _asset_equal(path, old, new):
    if old == new:
        return True
    try:
        if path.suffix == ".png":
            return _blob_equal(old, new)
        split = _ico_entries if path.suffix == ".ico" else _icns_entries
        olds, news = split(old), split(new)
        return len(olds) == len(news) and all(
            _blob_equal(a, b) for a, b in zip(olds, news))
    except (struct.error, zlib.error, IndexError):
        return False


# --- main -------------------------------------------------------------------

def build():
    """Render every raster target into memory: {path: bytes}."""
    source = (ART / "icon.svg").read_text(encoding="utf-8")

    def render(size):
        return svgrender.render(source, size)

    # Windows tray + app icon. Small sizes: 16 px at 100% DPI, scaling
    # through 20/24/32 at 125/150/200%.
    ico = ico_bytes(bmp_images=[render(s) for s in (16, 20, 24, 32, 40, 48)],
                    png_images=[render(s) for s in (64, 128, 256)])

    # macOS app icon.
    sizes = sorted({size for size, _t in _ICNS_SLOTS})
    icns = icns_bytes({s: render(s) for s in sizes})

    # macOS menu bar extra: the 19x18 canvas renders at exactly scale 1
    # (38x36 at exactly 2).
    template = (ART / "MenuExtraTemplate.svg").read_text(encoding="utf-8")

    return {
        ASSETS / "keyhac.ico": ico,
        ASSETS / "keyhac.icns": icns,
        ASSETS / "MenuExtraTemplate.png":
            png_bytes(svgrender.render(template, 19, 18)),
        ASSETS / "MenuExtraTemplate@2x.png":
            png_bytes(svgrender.render(template, 38, 36)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="verify the checked-in assets match the SVG "
                             "masters instead of writing them")
    args = parser.parse_args(argv)

    targets = build()

    if args.check:
        stale = [path for path, data in targets.items()
                 if not (path.exists()
                         and _asset_equal(path, path.read_bytes(), data))]
        for path in stale:
            print(f"stale: {path.relative_to(ROOT)}")
        if stale:
            print("Icon assets do not match the SVG masters; "
                  "re-run tools/make_icons.py and commit the result.")
            return 1
        print("Icon assets match the SVG masters.")
        return 0

    ASSETS.mkdir(parents=True, exist_ok=True)
    for path, data in targets.items():
        path.write_bytes(data)
    for f in sorted(ASSETS.iterdir()):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
