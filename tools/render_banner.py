"""Render doc/images/banner.svg to the JPEGs the GitHub Pages site serves.

banner.svg is the source kept in git; the site uses rasterized copies because
SNS / chat link previews (X, Facebook, Slack, Discord, …) ignore SVG in
``og:image``. Two outputs, both committed next to the SVG:

    banner.jpg     2400x600  — the 4:1 banner on the Pages index (2x for HiDPI),
                               on the page's white background so the rounded
                               corners blend in
    banner-og.jpg  1200x630  — the ``og:image`` card: the banner centered on its
                               own dark background at the 1.91:1 ratio link
                               previews use, so no service crops the side panels

Rendering goes through headless Google Chrome (``--screenshot``), then ``sips``
converts PNG to JPEG — both already present on a macOS dev machine, so the
repo gains no Python dependency. Re-run with ``make banner`` after editing the
SVG. ``CHROME=/path/to/chrome`` overrides the browser location.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "doc" / "images"
SVG = IMAGES / "banner.svg"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
]

# (output name, CSS width, CSS height, device scale, page background, padding)
TARGETS = [
    ("banner.jpg", 1200, 300, 2, "#ffffff", 0),
    ("banner-og.jpg", 1200, 630, 1, "#0d1117", 40),
]

JPEG_QUALITY = 90


def find_chrome() -> str:
    env = os.environ.get("CHROME")
    if env:
        return env
    for cand in CHROME_CANDIDATES:
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    sys.exit("render_banner: Google Chrome not found (set CHROME=/path/to/chrome)")


def page_html(svg: str, width: int, height: int, background: str, padding: int) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden;
               background: {background}; }}
  body {{ display: flex; align-items: center; justify-content: center;
          box-sizing: border-box; padding: {padding}px; }}
  svg {{ width: 100%; height: auto; display: block; }}
</style></head><body>{svg}</body></html>"""


def render(chrome: str, svg: str, name: str, width: int, height: int,
           scale: int, background: str, padding: int, tmp: Path) -> None:
    html = tmp / f"{name}.html"
    png = tmp / f"{name}.png"
    html.write_text(page_html(svg, width, height, background, padding), encoding="utf-8")
    subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={width},{height}",
         f"--force-device-scale-factor={scale}",
         f"--screenshot={png}", html.as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    out = IMAGES / name
    subprocess.run(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(JPEG_QUALITY),
         str(png), "--out", str(out)],
        check=True, stdout=subprocess.DEVNULL,
    )
    print(f"wrote {out.relative_to(ROOT)} ({width * scale}x{height * scale})")


def main() -> None:
    chrome = find_chrome()
    svg = SVG.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        for name, w, h, scale, bg, pad in TARGETS:
            render(chrome, svg, name, w, h, scale, bg, pad, Path(d))


if __name__ == "__main__":
    main()
