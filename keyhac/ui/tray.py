"""The menu bar extra / system tray icon."""

import inspect
import sys
from pathlib import Path

from puikit import Menu, MenuItem, SEPARATOR

from keyhac.core import log

logger = log.getLogger("Tray")

_ASSETS = Path(__file__).with_name("assets")


def _tray_image() -> str | None:
    """Path of the keycap icon (the keyhac-win app-icon design; vector
    sources maintained in art/, raster targets rendered by
    tools/make_icons.py): a color .ico for the Windows tray, and for the
    macOS menu bar extra the pre-rasterized template PNG — puikit pairs
    the @2x sibling and applies the AppKit "…Template" naming convention
    (alpha = ink, recolored by the system for dark mode / menu
    highlight). A bitmap rather than the SVG master itself: macOS caches
    a system-side rasterization of vector status-item images by file
    identity, and an in-place edit of the SVG left menu bars compositing
    the stale raster of the old artwork (see art/MenuExtraTemplate.svg)."""
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

    def open_guide():
        # Pinned to the running version, not main: the page tells an agent
        # which skill bundle to fetch and what this build's API looks like, and
        # main would hand it a newer answer than the Keyhac it is talking to.
        import keyhac
        url = (f"https://github.com/crftwr/keyhac/blob/v{keyhac.__version__}"
               f"/doc/ai-integration.md")
        if keymap.app_control is None:
            logger.info(f"AI integration guide: {url}")
            return
        keymap.app_control.open_url(url)

    def toggle_mcp():
        # Through the console's handler rather than the keymap's, so the
        # setting is written and the checkbox follows from one place - the two
        # switches are one switch with two faces.
        console._on_mcp_toggle(not keymap.mcp_server_running)
        console._mcp_checkbox.checked = keymap.mcp_server_running
        console.panel.render()

    def toggle_authoring():
        console._on_authoring_toggle(not keymap.action_authoring_allowed)
        console._authoring_checkbox.checked = keymap.action_authoring_allowed
        console.panel.render()

    menu = Menu(
        MenuItem("Open Console", on_select=console.backend.show_main_window),
        MenuItem("Edit Config", on_select=keymap.edit_config),
        MenuItem("Reload Config", on_select=keymap.configure),
        MenuItem("Keyboard Hook", on_select=toggle_hook,
                 checked=lambda: hook.installed),
        # Nested under a name that means something to someone who has never
        # heard of MCP. "MCP Server" at the top level is a row most users
        # cannot evaluate - they can neither want it nor avoid it - whereas
        # "AI Integration" says what the whole branch is for, and anyone who
        # needs the protocol's name finds it one level in.
        MenuItem("AI Integration", submenu=Menu(
            MenuItem("MCP Server", on_select=toggle_mcp,
                     checked=lambda: keymap.mcp_server_running),
            # Its own row rather than something the server switch implies. The
            # tick is evaluated when the menu opens, so a window that has since
            # run out reads as off without anything having to push it.
            MenuItem("Allow action authoring", on_select=toggle_authoring,
                     checked=lambda: keymap.action_authoring_allowed),
            SEPARATOR,
            # The setup instructions are the thing you hand to an agent, so
            # what this really provides is the URL - the page's first line
            # tells the reader to pass it on rather than follow it themselves.
            MenuItem("Setup Guide", on_select=open_guide),
        )),
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
