"""The menu bar extra / system tray icon."""

import inspect
import sys
from pathlib import Path

from puikit import Menu, MenuItem, SEPARATOR

_ASSETS = Path(__file__).with_name("assets")


def _tray_image() -> str | None:
    """Path of the keycap icon (the keyhac-win app-icon design; vector
    sources maintained in art/icon.svg and assets/MenuExtraTemplate.svg,
    raster targets rendered by tools/make_icons.py): a color .ico for the
    Windows tray, and for the macOS menu bar extra the template SVG itself
    (NSImage loads SVG natively on macOS 11+) — menu extras must be
    monochrome, so the keycap is drawn as solid line art (alpha only from
    anti-aliasing) and the system recolors the ink for dark mode / menu
    highlight."""
    if sys.platform == "darwin":
        return str(_ASSETS / "MenuExtraTemplate.svg")
    if sys.platform == "win32":
        return str(_ASSETS / "keyhac.ico")
    return None


def install_tray(console, keymap, hook) -> None:
    def toggle_hook():
        console._on_hook_toggle(not hook.installed)
        console._hook_checkbox.checked = hook.installed
        console.panel.render()

    menu = Menu(
        MenuItem("Open Console", on_select=console.backend.show_main_window),
        MenuItem("Reload Config", on_select=keymap.configure),
        MenuItem("Keyboard Hook", on_select=toggle_hook,
                 checked=lambda: hook.installed),
        SEPARATOR,
        MenuItem("Quit Keyhac", on_select=console.backend.quit),
    )
    image = _tray_image()
    # ``image`` is a puikit addition still in review (puikit PR #82); until it
    # ships, degrade to the pre-image behavior (Windows shows the host exe's
    # embedded icon, macOS the title glyph).
    set_tray = console.backend.set_tray
    if "image" not in inspect.signature(set_tray).parameters:
        image = None
    if image is None:
        set_tray("⌨", menu, tooltip="Keyhac")
    else:
        set_tray(None, menu, tooltip="Keyhac", image=image)
