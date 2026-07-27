"""Pure-stdlib rasterizer for the SVG subset Keyhac's icon sources use.

Why hand-rolled: the icon assets must be regenerable on both macOS and
Windows from a plain Python checkout — no NSImage (macOS-only), no Direct2D
(Windows-only), no cairo/resvg wheels. The artwork is ours, so the renderer
only has to cover the subset we author in ``art/`` and fails loudly on
anything outside it, rather than guessing.

Supported subset
----------------
- elements: ``svg`` (with ``viewBox``), ``g``, ``path``; ``defs``/``title``/
  ``desc``/``metadata`` are skipped
- ``path d``: M/m L/l H/h V/v C/c S/s Q/q T/t Z/z (no arcs)
- ``transform``: matrix / translate / scale / rotate, composed down the tree
- paint: ``fill`` / ``stroke`` as ``#rgb`` / ``#rrggbb`` / ``black`` /
  ``white`` / ``none``, with ``fill-opacity`` / ``stroke-opacity`` /
  ``opacity`` (group opacity is approximated as a per-shape multiply) and
  ``fill-rule`` nonzero / evenodd; ``style="…"`` presentation shorthand works
- ``stroke-width``; every stroke is drawn with *round* caps and joins
  (``stroke-linecap`` / ``stroke-linejoin`` values are accepted but not
  distinguished — the icon artwork is designed for round)

Rendering: shapes are flattened to polygons (strokes become unions of
per-segment round-capped capsules) and scanline-filled with vertical
supersampling plus exact horizontal span coverage, composited src-over in
document order. Output is a row-major grid of straight-alpha RGBA tuples.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from array import array

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_PATH_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtZzAa])|" + _NUM.pattern)
_CURVE_STEPS = 48
_CAP_STEPS = 12  # half-circle segments on each capsule end

_SKIP_TAGS = {"defs", "title", "desc", "metadata"}
_NAMED_COLORS = {"black": (0, 0, 0), "white": (255, 255, 255)}


class UnsupportedSVG(ValueError):
    """The file uses a feature outside the documented subset."""


# --- affine transforms (a, b, c, d, e, f): x' = ax + cy + e, y' = bx + dy + f

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat_mul(m, n):
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _scale_of(m):
    """Uniform length-scale factor of an affine map (for stroke widths)."""
    a, b, c, d, _e, _f = m
    return math.sqrt(abs(a * d - b * c))


def _parse_transform(text):
    m = _IDENTITY
    for op, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        v = [float(t) for t in _NUM.findall(args)]
        if op == "matrix" and len(v) == 6:
            n = tuple(v)
        elif op == "translate" and len(v) in (1, 2):
            n = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) == 2 else 0.0)
        elif op == "scale" and len(v) in (1, 2):
            n = (v[0], 0.0, 0.0, v[1] if len(v) == 2 else v[0], 0.0, 0.0)
        elif op == "rotate" and len(v) == 1:
            r = math.radians(v[0])
            n = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)
        else:
            raise UnsupportedSVG(f"transform {op}({args})")
        m = _mat_mul(m, n)
    return m


# --- styling ----------------------------------------------------------------

def _element_style(el):
    """Presentation attributes merged with the style="" shorthand (style
    wins, matching CSS precedence)."""
    style = dict(el.attrib)
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            key, value = decl.split(":", 1)
            style[key.strip()] = value.strip()
    return style


def _parse_color(text):
    if text is None:
        return None
    text = text.strip()
    if text == "none":
        return None
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    if text.startswith("#") and len(text) == 4:
        return tuple(int(ch * 2, 16) for ch in text[1:])
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
    raise UnsupportedSVG(f"color {text!r}")


# --- path data --------------------------------------------------------------

def _flatten_curve(points, control_points):
    """Append a Bezier (any degree via de Casteljau) as line points."""
    for i in range(1, _CURVE_STEPS + 1):
        t = i / _CURVE_STEPS
        layer = control_points
        while len(layer) > 1:
            layer = [((1 - t) * ax + t * bx, (1 - t) * ay + t * by)
                     for (ax, ay), (bx, by) in zip(layer, layer[1:])]
        points.append(layer[0])


def _parse_path(d):
    """-> list of (points, closed) subpaths, curves flattened."""
    stream = [match.group(0) for match in _PATH_TOKEN.finditer(d)]

    subpaths = []
    points = []
    command = None
    start = current = (0.0, 0.0)
    last_cubic_control = last_quad_control = None
    i = 0

    def take(n):
        nonlocal i
        values = [float(v) for v in stream[i:i + n]]
        if len(values) != n:
            raise UnsupportedSVG(f"path data ends mid-{command}")
        i += n
        return values

    def flush(closed):
        nonlocal points
        if len(points) > 1:
            subpaths.append((points, closed))
        points = []

    while i < len(stream):
        token = stream[i]
        if token[0].isalpha():
            command = token
            i += 1
            if command in "Aa":
                raise UnsupportedSVG("path arc (A) commands")
        elif command is None:
            raise UnsupportedSVG("path data before any command")
        relative = command.islower()
        op = command.upper()
        ox, oy = current if relative else (0.0, 0.0)

        if op == "Z":
            flush(True)
            current = start
            command = None
            last_cubic_control = last_quad_control = None
            continue
        if op == "M":
            x, y = take(2)
            flush(False)
            current = start = (x + ox, y + oy)
            points = [current]
            command = "l" if relative else "L"  # implicit lineto after M
            last_cubic_control = last_quad_control = None
            continue

        if op == "L":
            x, y = take(2)
            current = (x + ox, y + oy)
        elif op == "H":
            (x,) = take(1)
            current = (x + ox, current[1])
        elif op == "V":
            (y,) = take(1)
            current = (current[0], y + oy)
        elif op in ("C", "S"):
            if op == "C":
                x1, y1, x2, y2, x, y = take(6)
                c1 = (x1 + ox, y1 + oy)
            else:
                x2, y2, x, y = take(4)
                c1 = ((2 * current[0] - last_cubic_control[0],
                       2 * current[1] - last_cubic_control[1])
                      if last_cubic_control else current)
            c2 = (x2 + ox, y2 + oy)
            end = (x + ox, y + oy)
            _flatten_curve(points, [current, c1, c2, end])
            current = end
            last_cubic_control, last_quad_control = c2, None
            continue
        elif op in ("Q", "T"):
            if op == "Q":
                x1, y1, x, y = take(4)
                c1 = (x1 + ox, y1 + oy)
            else:
                (x, y) = take(2)
                c1 = ((2 * current[0] - last_quad_control[0],
                       2 * current[1] - last_quad_control[1])
                      if last_quad_control else current)
            end = (x + ox, y + oy)
            _flatten_curve(points, [current, c1, end])
            current = end
            last_quad_control, last_cubic_control = c1, None
            continue
        else:
            raise UnsupportedSVG(f"path command {command!r}")
        points.append(current)
        last_cubic_control = last_quad_control = None

    flush(False)
    return subpaths


# --- stroke -> polygons -----------------------------------------------------

def _capsule(a, b, radius):
    """Round-capped stroke of one segment as a polygon."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1e-12:
        return [(ax + radius * math.cos(2 * math.pi * k / (4 * _CAP_STEPS)),
                 ay + radius * math.sin(2 * math.pi * k / (4 * _CAP_STEPS)))
                for k in range(4 * _CAP_STEPS)]
    ux, uy = dx / length, dy / length
    base = math.atan2(uy, ux) + math.pi / 2  # normal side of A
    poly = []
    for k in range(_CAP_STEPS + 1):  # half circle around A, away from B
        angle = base + math.pi * k / _CAP_STEPS
        poly.append((ax + radius * math.cos(angle),
                     ay + radius * math.sin(angle)))
    for k in range(_CAP_STEPS + 1):  # half circle around B, away from A
        angle = base + math.pi + math.pi * k / _CAP_STEPS
        poly.append((bx + radius * math.cos(angle),
                     by + radius * math.sin(angle)))
    return poly


def _stroke_polygons(subpaths, width):
    radius = width / 2.0
    polygons = []
    for points, closed in subpaths:
        pairs = list(zip(points, points[1:]))
        if closed and points[0] != points[-1]:
            pairs.append((points[-1], points[0]))
        for a, b in pairs:
            polygons.append(_capsule(a, b, radius))
    return polygons


# --- scanline fill ----------------------------------------------------------

def _coverage(polygons, rule, width, height, supersample):
    """Antialiased coverage of a polygon set: per output row, ``supersample``
    scanlines of winding-rule spans with exact horizontal partial-pixel
    coverage. Returns (rows, row_bounds) where rows[y] is an array of floats
    and row_bounds[y] the touched [x0, x1) range (None when empty)."""
    edges = []
    for poly in polygons:
        closed = poly if poly[0] == poly[-1] else poly + [poly[0]]
        for (x1, y1), (x2, y2) in zip(closed, closed[1:]):
            if y1 != y2:
                edges.append((min(y1, y2), max(y1, y2), x1, y1, x2, y2,
                              1 if y2 > y1 else -1))
    rows = [None] * height
    bounds = [None] * height
    if not edges:
        return rows, bounds
    y_min = max(0, int(min(e[0] for e in edges)))
    y_max = min(height, int(max(e[1] for e in edges)) + 1)
    weight = 1.0 / supersample

    for y in range(y_min, y_max):
        row = None
        lo = hi = 0
        for k in range(supersample):
            yc = y + (k + 0.5) / supersample
            crossings = []
            for e_min, e_max, x1, y1, x2, y2, direction in edges:
                if e_min <= yc < e_max:
                    crossings.append(
                        (x1 + (x2 - x1) * (yc - y1) / (y2 - y1), direction))
            if not crossings:
                continue
            crossings.sort()
            spans = []
            if rule == "evenodd":
                for j in range(0, len(crossings) - 1, 2):
                    spans.append((crossings[j][0], crossings[j + 1][0]))
            else:  # nonzero
                winding = 0
                span_start = 0.0
                for x, direction in crossings:
                    if winding == 0:
                        span_start = x
                    winding += direction
                    if winding == 0:
                        spans.append((span_start, x))
            for xa, xb in spans:
                xa = max(xa, 0.0)
                xb = min(xb, float(width))
                if xb <= xa:
                    continue
                if row is None:
                    row = array("d", bytes(8 * width))
                    lo, hi = int(xa), min(int(xb) + 1, width)
                ia, ib = int(xa), min(int(xb), width - 1)
                lo, hi = min(lo, ia), max(hi, ib + 1)
                if ia == ib:
                    row[ia] += (xb - xa) * weight
                else:
                    row[ia] += (ia + 1 - xa) * weight
                    for x in range(ia + 1, ib):
                        row[x] += weight
                    row[ib] += (xb - ib) * weight
        if row is not None:
            rows[y] = row
            bounds[y] = (lo, hi)
    return rows, bounds


# --- document walk ----------------------------------------------------------

def _collect_ops(el, matrix, inherited, ops):
    tag = el.tag.rsplit("}", 1)[-1]
    if tag in _SKIP_TAGS:
        return
    style = dict(inherited)
    own = _element_style(el)
    for key in ("fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
                "stroke-opacity", "stroke-linecap", "stroke-linejoin"):
        if key in own:
            style[key] = own[key]
    # Group opacity approximated as a multiplier down the tree.
    style["_opacity"] = inherited.get("_opacity", 1.0) * float(
        own.get("opacity", 1.0))
    matrix = _mat_mul(matrix, _parse_transform(own.get("transform")))

    if tag in ("svg", "g"):
        for child in el:
            _collect_ops(child, matrix, style, ops)
        return
    if tag != "path":
        raise UnsupportedSVG(f"element <{tag}>")

    subpaths = [([_apply(matrix, x, y) for x, y in points], closed)
                for points, closed in _parse_path(own.get("d", ""))]
    if not subpaths:
        return
    opacity = style["_opacity"]

    fill = _parse_color(style.get("fill", "black"))
    if fill is not None:
        alpha = float(style.get("fill-opacity", 1.0)) * opacity
        ops.append(([points for points, _closed in subpaths],
                    style.get("fill-rule", "nonzero"), fill, alpha))
    stroke = _parse_color(style.get("stroke"))
    if stroke is not None:
        stroke_width = float(style.get("stroke-width", 1.0)) * _scale_of(matrix)
        alpha = float(style.get("stroke-opacity", 1.0)) * opacity
        ops.append((_stroke_polygons(subpaths, stroke_width),
                    "nonzero", stroke, alpha))


# --- public API -------------------------------------------------------------

def render(svg_text, width, height=None, supersample=8):
    """Rasterize to a width x height grid of (r, g, b, a) byte tuples. The
    viewBox is fit uniformly and centered (preserveAspectRatio meet)."""
    height = width if height is None else height
    root = ET.fromstring(svg_text)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise UnsupportedSVG("root element is not <svg>")
    view_box = root.get("viewBox")
    if not view_box:
        raise UnsupportedSVG("missing viewBox")
    vx, vy, vw, vh = (float(v) for v in _NUM.findall(view_box))
    scale = min(width / vw, height / vh)
    device = (scale, 0.0, 0.0, scale,
              (width - vw * scale) / 2 - vx * scale,
              (height - vh * scale) / 2 - vy * scale)

    ops = []
    for child in root:
        _collect_ops(child, device, {}, ops)

    # Premultiplied float buffer, composited src-over per op.
    buffer = [array("d", bytes(8 * 4 * width)) for _ in range(height)]
    for polygons, rule, (r, g, b), alpha in ops:
        rows, bounds = _coverage(polygons, rule, width, height, supersample)
        for y in range(height):
            if rows[y] is None:
                continue
            cov = rows[y]
            out = buffer[y]
            lo, hi = bounds[y]
            for x in range(lo, hi):
                ca = alpha * cov[x]
                if ca <= 0.0:
                    continue
                if ca > 1.0:
                    ca = 1.0
                inv = 1.0 - ca
                base = 4 * x
                out[base] = r * ca + out[base] * inv
                out[base + 1] = g * ca + out[base + 1] * inv
                out[base + 2] = b * ca + out[base + 2] * inv
                out[base + 3] = ca + out[base + 3] * inv

    pixels = []
    for y in range(height):
        src = buffer[y]
        row = []
        for x in range(width):
            base = 4 * x
            a = src[base + 3]
            if a <= 0.0:
                row.append((0, 0, 0, 0))
            else:
                row.append((min(255, round(src[base] / a)),
                            min(255, round(src[base + 1] / a)),
                            min(255, round(src[base + 2] / a)),
                            min(255, round(255 * a))))
        pixels.append(row)
    return pixels


def render_file(path, width, height=None, supersample=8):
    with open(path, encoding="utf-8") as f:
        return render(f.read(), width, height, supersample)
