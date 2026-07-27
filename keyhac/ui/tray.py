"""The menu bar extra / system tray icon."""

import sys
from pathlib import Path

from puikit import Menu, MenuItem, SEPARATOR

_ASSETS = Path(__file__).with_name("assets")


def _tray_image() -> str | None:
    """Path of the keycap icon (the keyhac-win app-icon design, regenerated
    by tools/make_tray_icons.py): a color .ico for the Windows tray, and for
    the macOS menu bar extra a grayscale AppKit template PNG — menu extras
    must be monochrome, so the face shading rides in the alpha channel and
    the system recolors it for dark mode / menu highlight."""
    if sys.platform == "darwin":
        return str(_ASSETS / "MenuExtraTemplate.png")
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
    console.backend.set_tray("⌨" if image is None else None, menu,
                             tooltip="Keyhac", image=image)
