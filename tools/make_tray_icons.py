"""Generate the tray / menu-bar-extra icon assets from the Keyhac keycap design.

The geometry is the keycap from keyhac-win's ``icon.svg`` (group ``g3216``),
with the SVG group transform applied so all coordinates live in one space:
a hexagonal silhouette (a keycap seen from the front-top) drawn with a thick
black outline, its interior split by thinner seams into four faces — left,
top, front, right — each a different shade of lavender.

Outputs (all checked in; re-run this script only to change the design):

- ``keyhac/ui/assets/keyhac.ico``          — color, 16/20/24/32/40/48 px,
  32bpp BMP entries. Used by the Windows system tray (and any HWND icon).
- ``keyhac/ui/assets/MenuExtraTemplate.svg`` — template for the macOS menu
  bar extra (NSImage loads SVG natively on macOS 11+, so no @2x raster pair
  is needed): black ink whose opacity encodes the face shading, so AppKit
  can recolor it for menu bar state (dark mode, highlight). The
  ``…Template`` name is the AppKit convention puikit's ``set_tray(image=…)``
  keys on. keyhac-win's own ``icon.svg`` cannot be used here: its faces are
  opaque color fills, and template rendering keeps only alpha — it would
  show as a solid silhouette.

Pure stdlib (zlib/struct/math): polygons are point-sampled on a supersampled
grid against a distance field, so the thick outline gets the same rounded
corners the original icon has.

Usage: ``.venv/bin/python tools/make_tray_icons.py``
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "keyhac" / "ui" / "assets"

# --- geometry (icon.svg outer coordinate space) -----------------------------

# Silhouette centerline: top edge, right shoulder, bottom edge, left shoulder.
SILHOUETTE = [
    (240.0, 312.4),   # top-left
    (470.0, 312.4),   # top-right
    (544.0, 364.0),   # right shoulder
    (583.0, 658.0),   # bottom-right
    (134.0, 658.5),   # bottom-left
    (172.5, 365.0),   # left shoulder
]

# The seam corners where the key top meets the front face.
_TOP_BL = (220.0, 512.4)
_TOP_BR = (490.0, 512.4)

# Interior faces tile the silhouette; seams between them are drawn by
# distance to the SEAMS segments, so faces share exact corner coordinates.
FACES = {
    "left": [(240.0, 312.4), _TOP_BL, (134.0, 658.5), (172.5, 365.0)],
    "top": [(240.0, 312.4), (470.0, 312.4), _TOP_BR, _TOP_BL],
    "right": [(470.0, 312.4), (544.0, 364.0), (583.0, 658.0), _TOP_BR],
    "front": [_TOP_BL, _TOP_BR, (583.0, 658.0), (134.0, 658.5)],
}

SEAMS = [
    ((240.0, 312.4), _TOP_BL),   # key-top left edge
    (_TOP_BL, _TOP_BR),          # key-top bottom edge
    ((470.0, 312.4), _TOP_BR),   # key-top right edge
    (_TOP_BL, (134.0, 658.5)),   # front/left split
    (_TOP_BR, (583.0, 658.0)),   # front/right split
]

OUTLINE_HALF = 30.0   # icon.svg: stroke-width 60
SEAM_HALF = 7.5       # icon.svg: stroke-width 15

# Face fills from icon.svg.
COLOR_FACES = {
    "left": (233, 233, 255, 255),    # e9e9ff
    "top": (175, 175, 222, 255),     # afafde
    "right": (134, 134, 191, 255),   # 8686bf
    "front": (215, 215, 255, 255),   # d7d7ff
}
COLOR_INK = (0, 0, 0, 255)

# Template: black ink, face shading mapped to alpha (ink = darker face,
# more ink). Tuned so the key top reads as a surface, not a hole.
TEMPLATE_FACES = {
    "left": (0, 0, 0, 50),
    "top": (0, 0, 0, 96),
    "right": (0, 0, 0, 128),
    "front": (0, 0, 0, 62),
}
TEMPLATE_INK = (0, 0, 0, 255)

# Faces drawn before the strokes, so partial-opacity ink composites over
# transparency (never over the outline's black, which would darken it).
_FACE_ORDER = ("left", "top", "right", "front")

TRANSPARENT = (0, 0, 0, 0)


# --- sampling ---------------------------------------------------------------

def _dist_to_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    t = (wx * vx + wy * vy) / (vx * vx + vy * vy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    dx, dy = wx - t * vx, wy - t * vy
    return math.hypot(dx, dy)


def _in_polygon(px, py, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _boundary_dist(px, py, poly):
    j = len(poly) - 1
    d = math.inf
    for i in range(len(poly)):
        d = min(d, _dist_to_segment(px, py, *poly[j], *poly[i]))
        j = i
    return d


def _sample(px, py, faces, ink, outline_half, seam_half):
    """Color of one geometry-space point."""
    d = _boundary_dist(px, py, SILHOUETTE)
    if not _in_polygon(px, py, SILHOUETTE):
        return ink if d <= outline_half else TRANSPARENT
    if d <= outline_half:
        return ink
    for a, b in SEAMS:
        if _dist_to_segment(px, py, *a, *b) <= seam_half:
            return ink
    for name, poly in FACES.items():
        if _in_polygon(px, py, poly):
            return faces[name]
    return ink  # razor-thin numeric gap between faces: treat as seam


def render(size, faces, ink, *, content_frac=0.94, supersample=8,
           outline_half=OUTLINE_HALF, seam_half=SEAM_HALF):
    """Render the keycap centered on a size x size RGBA canvas.

    ``content_frac`` is the fraction of the canvas width the keycap spans
    (it is wider than tall, so width is the binding dimension).
    """
    xs = [p[0] for p in SILHOUETTE]
    ys = [p[1] for p in SILHOUETTE]
    x0, x1 = min(xs) - outline_half, max(xs) + outline_half
    y0, y1 = min(ys) - outline_half, max(ys) + outline_half
    scale = (x1 - x0) / (size * content_frac)
    # Center the content box on the canvas (in geometry units).
    ox = (x0 + x1) / 2 - size * scale / 2
    oy = (y0 + y1) / 2 - size * scale / 2

    ss = supersample
    pixels = []
    for y in range(size):
        row = []
        for x in range(size):
            # Average premultiplied samples so the black outline does not
            # bleed a dark halo into transparent neighbors.
            pr = pg = pb = pa = 0.0
            for sy in range(ss):
                gy = oy + (y + (sy + 0.5) / ss) * scale
                for sx in range(ss):
                    gx = ox + (x + (sx + 0.5) / ss) * scale
                    r, g, b, a = _sample(gx, gy, faces, ink,
                                         outline_half, seam_half)
                    pr += r * a
                    pg += g * a
                    pb += b * a
                    pa += a
            n = ss * ss
            if pa == 0.0:
                row.append((0, 0, 0, 0))
            else:
                row.append((round(pr / pa), round(pg / pa), round(pb / pa),
                            round(pa / n)))
        pixels.append(row)
    return pixels


# --- encoders ---------------------------------------------------------------

def write_png(path, pixels):
    h = len(pixels)
    w = len(pixels[0])
    raw = b"".join(
        b"\x00" + b"".join(bytes(px) for px in row) for row in pixels
    )

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def write_ico(path, images):
    """``images``: list of pixel grids (square), written as 32bpp BMP entries
    (with the classic doubled-height header + empty AND mask) for maximum
    shell compatibility."""
    entries = []
    blobs = []
    for pixels in images:
        n = len(pixels)
        # Bottom-up BGRA rows.
        xor = b"".join(
            bytes(c for px in row for c in (px[2], px[1], px[0], px[3]))
            for row in reversed(pixels)
        )
        and_stride = ((n + 31) // 32) * 4
        blob = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0,
                           len(xor), 0, 0, 0, 0) + xor + b"\x00" * (and_stride * n)
        entries.append((n, blob))
        blobs.append(blob)

    offset = 6 + 16 * len(images)
    directory = struct.pack("<HHH", 0, 1, len(images))
    for n, blob in entries:
        directory += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32,
                                 len(blob), offset)
        offset += len(blob)
    path.write_bytes(directory + b"".join(blobs))


def write_template_svg(path, size=18, content_frac=0.94):
    """The macOS menu-extra template as vector: the same geometry in black,
    each face's raster alpha expressed as fill-opacity. Draw order is faces,
    then the outline stroke, then the seams — partial-opacity ink must sit
    on transparency, and the strokes then cover the face edges so no
    hairline gaps appear between adjacent fills."""
    xs = [p[0] for p in SILHOUETTE]
    ys = [p[1] for p in SILHOUETTE]
    x0, x1 = min(xs) - OUTLINE_HALF, max(xs) + OUTLINE_HALF
    y0, y1 = min(ys) - OUTLINE_HALF, max(ys) + OUTLINE_HALF
    s = size * content_frac / (x1 - x0)
    tx = (size - (x1 - x0) * s) / 2 - x0 * s
    ty = (size - (y1 - y0) * s) / 2 - y0 * s

    def outline_of(poly, close=" Z"):
        return "M" + " L".join(f"{x:g} {y:g}" for x, y in poly) + close

    faces = "\n".join(
        f'    <path d="{outline_of(FACES[name])}" fill="#000"'
        f' fill-opacity="{TEMPLATE_FACES[name][3] / 255:.3f}"/>'
        for name in _FACE_ORDER)
    seams = " ".join(f"M{ax:g} {ay:g} L{bx:g} {by:g}"
                     for (ax, ay), (bx, by) in SEAMS)
    path.write_text(f"""\
<!-- Generated by tools/make_tray_icons.py - do not edit by hand. -->
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"
     viewBox="0 0 {size} {size}">
  <g transform="translate({tx:.4f} {ty:.4f}) scale({s:.6f})">
{faces}
    <path d="{outline_of(SILHOUETTE)}" fill="none" stroke="#000"
          stroke-width="{OUTLINE_HALF * 2:g}" stroke-linejoin="round"/>
    <path d="{seams}" fill="none" stroke="#000"
          stroke-width="{SEAM_HALF * 2:g}"/>
  </g>
</svg>
""")


# --- main -------------------------------------------------------------------

def main():
    ASSETS.mkdir(parents=True, exist_ok=True)

    # Windows tray: the small-icon size is 16 px at 100% DPI, scaling through
    # 20/24/32 at 125/150/200%; 40/48 cover large-icon uses of the same .ico.
    ico_sizes = (16, 20, 24, 32, 40, 48)
    write_ico(ASSETS / "keyhac.ico",
              [render(s, COLOR_FACES, COLOR_INK) for s in ico_sizes])

    # macOS menu bar extra: an 18 pt vector template (macOS 11+ NSImage
    # loads SVG natively; being vector, Retina needs no @2x pair).
    write_template_svg(ASSETS / "MenuExtraTemplate.svg")

    for f in sorted(ASSETS.iterdir()):
        print(f"{f.relative_to(ASSETS.parent.parent.parent)}  "
              f"{f.stat().st_size} bytes")


if __name__ == "__main__":
    main()
