"""tools/svgrender.py — the pure-stdlib SVG rasterizer behind the icon
build. It renders artwork we author ourselves, so these tests pin the
subset's semantics (winding, AA coverage, transforms, stroking) and that
anything outside the subset fails loudly instead of rendering wrong."""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))

import make_icons  # noqa: E402
import svgrender  # noqa: E402
from svgrender import UnsupportedSVG, render  # noqa: E402


def _svg(body, size=8):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {size} {size}">{body}</svg>')


def test_full_cover_fill_defaults_to_black():
    pixels = render(_svg('<path d="M0 0 L8 0 L8 8 L0 8 Z"/>'), 8)
    assert pixels[4][4] == (0, 0, 0, 255)
    assert pixels[0][0] == (0, 0, 0, 255)


def test_edge_antialiasing_covers_half_pixel():
    # Right edge at x=4.5: column 3 full ink, column 4 half, column 5 empty.
    pixels = render(_svg('<path d="M0 0 L4.5 0 L4.5 8 L0 8 Z"/>'), 8)
    assert pixels[4][3][3] == 255
    assert abs(pixels[4][4][3] - 128) <= 2
    assert pixels[4][5][3] == 0


def test_fill_opacity_and_hex_colors():
    pixels = render(_svg(
        '<path d="M0 0 L8 0 L8 8 L0 8 Z" fill="#afafde" fill-opacity="0.5"/>'), 8)
    r, g, b, a = pixels[4][4]
    assert (r, g, b) == (175, 175, 222)
    assert abs(a - 128) <= 1


def test_transforms_compose_down_the_tree():
    pixels = render(_svg(
        '<g transform="translate(4 0)"><g transform="scale(0.5)">'
        '<path d="M0 0 L8 0 L8 8 L0 8 Z"/></g></g>'), 8)
    # The unit square lands on x 4..8, y 0..4.
    assert pixels[1][6][3] == 255
    assert pixels[1][2][3] == 0
    assert pixels[6][6][3] == 0


def test_stroke_is_a_round_capped_band():
    pixels = render(_svg(
        '<path d="M4 2 L4 6" fill="none" stroke="black" stroke-width="2"/>'), 8)
    assert pixels[4][4][3] == 255          # on the line
    assert pixels[4][0][3] == 0            # far left
    assert pixels[0][4][3] == 0            # beyond the cap
    assert pixels[1][4][3] > 0             # inside the round cap (y=1.5 > 2-1)


def test_evenodd_leaves_a_hole_where_nonzero_fills():
    ring = ('<path d="M0 0 L8 0 L8 8 L0 8 Z M2 2 L6 2 L6 6 L2 6 Z" '
            'fill-rule="{}"/>')
    assert render(_svg(ring.format("evenodd")), 8)[4][4][3] == 0
    assert render(_svg(ring.format("nonzero")), 8)[4][4][3] == 255


def test_viewbox_fits_uniformly_and_centers():
    # 8x4 viewBox in an 8x8 target: content occupies rows 2..5.
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 4">'
           '<path d="M0 0 L8 0 L8 4 L0 4 Z"/></svg>')
    pixels = render(svg, 8)
    assert pixels[1][4][3] == 0
    assert pixels[2][4][3] == 255
    assert pixels[5][4][3] == 255
    assert pixels[6][4][3] == 0


def test_unsupported_features_fail_loudly():
    with pytest.raises(UnsupportedSVG):
        render(_svg('<circle cx="4" cy="4" r="3"/>'), 8)
    with pytest.raises(UnsupportedSVG):
        render(_svg('<path d="M0 0 A 4 4 0 0 1 8 8"/>'), 8)
    with pytest.raises(UnsupportedSVG):
        render(_svg('<path d="M0 0 L8 8" fill="url(#g)"/>'), 8)
    with pytest.raises(UnsupportedSVG):
        render('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>', 8)


def test_rendering_is_deterministic():
    svg = (_ROOT / "art" / "icon.svg").read_text()
    assert render(svg, 24) == render(svg, 24)


def test_icon_svg_renders_all_four_faces_and_outline():
    colors = {px[:3] for row in svgrender.render_file(_ROOT / "art" / "icon.svg", 64)
              for px in row if px[3] == 255}
    for expected in ((233, 233, 255), (175, 175, 222),
                     (134, 134, 191), (215, 215, 255), (0, 0, 0)):
        assert expected in colors


def test_menu_extra_template_stays_in_the_subset():
    # The icon build rasterizes the template with this same render call.
    # Line-art template: outline and key-top edge lines are solid ink and
    # every face stays open (the menu bar shows through).
    pixels = svgrender.render_file(_ROOT / "art" / "MenuExtraTemplate.svg",
                                   42, 36)

    def alpha(x_pt, y_pt):  # canvas pt -> 2x pixel
        return pixels[round(y_pt * 2)][round(x_pt * 2)][3]

    # Art coords map to canvas pt via x*0.039293 - 3.8865 / y*0.039293 - 10.0748.
    assert alpha(10.2, 3.08) == 255   # top outline (y=334.9)
    assert alpha(10.2, 9.93) == 255   # key-top bottom edge line (y=508.9)
    assert alpha(10.2, 6.5) == 0      # top face open
    assert alpha(10.2, 12.5) == 0     # bottom face open


def test_ico_container_layout(tmp_path):
    import struct
    square = [[(255, 0, 0, 255)] * 4 for _ in range(4)]
    big = [[(255, 0, 0, 255)] * 8 for _ in range(8)]
    path = tmp_path / "t.ico"
    make_icons.write_ico(path, bmp_images=[square], png_images=[big])
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind, count) == (0, 1, 2)
    # Second entry is the PNG one: its payload starts with the PNG magic.
    _w, _h, _c, _r, _p, _bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 22)
    assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
    assert offset + size == len(data)


def test_icns_container_layout(tmp_path):
    import struct
    renders = {size: [[(0, 0, 0, 255)] * size for _ in range(size)]
               for size, _t in make_icons._ICNS_SLOTS}
    path = tmp_path / "t.icns"
    make_icons.write_icns(path, renders)
    data = path.read_bytes()
    assert data[:4] == b"icns"
    assert struct.unpack_from(">I", data, 4)[0] == len(data)
    types = []
    pos = 8
    while pos < len(data):
        types.append(data[pos:pos + 4])
        pos += struct.unpack_from(">I", data, pos + 4)[0]
    assert pos == len(data)
    assert types == [t for _size, t in make_icons._ICNS_SLOTS]
