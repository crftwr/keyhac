"""The menu bar extra / system tray icon."""

import inspect
import sys
from pathlib import Path

from puikit import Menu, MenuItem, SEPARATOR

from keyhac.core import log

logger = log.getLogger("Tray")

_ASSETS = Path(__file__).with_name("assets")


#: The unread-error badge: a red dot on the tray icon (#111). Drawn by PuiKit
#: over the image, so the macOS menu bar extra stays a template.
_ERROR_BADGE = (230, 50, 50)


def _tray_image(busy: bool = False) -> str | None:
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
        name = "MenuExtraBusyTemplate.png" if busy else "MenuExtraTemplate.png"
        return str(_ASSETS / name)
    if sys.platform == "win32":
        return str(_ASSETS / ("keyhac-busy.ico" if busy else "keyhac.ico"))
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

    def toggle_mcp_timeout():
        # Through the console for the same reason toggle_mcp goes through it:
        # one place owns the setting and the running endpoint together.
        console.set_mcp_timeout_disabled(not console.mcp_timeout_disabled())
        console._mcp_checkbox.checked = keymap.mcp_server_running
        console.panel.render()

    items = (
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
            # The tick is evaluated when the menu opens, so an endpoint that
            # has since timed out reads as off without anything pushing it.
            MenuItem("MCP Server", on_select=toggle_mcp,
                     checked=lambda: keymap.mcp_server_running),
            # Under the switch it modifies, rather than beside it in the
            # console's toolbar: that row already spells its own hierarchy in
            # a label ("AI Integration: MCP Server") because a flat row cannot
            # show it, and a third peer there would read as a third unrelated
            # switch. A menu nests for free.
            MenuItem("No timeout", on_select=toggle_mcp_timeout,
                     checked=console.mcp_timeout_disabled),
            SEPARATOR,
            # The setup instructions are the thing you hand to an agent, so
            # what this really provides is the URL - the page's first line
            # tells the reader to pass it on rather than follow it themselves.
            MenuItem("Setup Guide", on_select=open_guide),
        )),
        SEPARATOR,
        MenuItem("Quit Keyhac", on_select=console.backend.quit),
    )
    indicator = _TrayIndicator(console, items)
    indicator.show((), 0)
    console.state_listeners.append(indicator.show)


class _TrayIndicator:
    """The tray icon, its tooltip and its menu, redrawn from Keyhac's state.

    Two states the user should be able to see without opening the console:
    an action running (#141) - the busy keycap, and a menu entry per action
    to stop it - and errors the console showed while nobody was looking
    (#111) - a red dot, and a menu entry that opens the console, which is
    what marks them read. Called on every console tick; redraws only when
    the state changed, since set_tray rebuilds the native menu.
    """

    def __init__(self, console, items):
        self._console = console
        self._items = items
        self._shown = None
        set_tray = console.backend.set_tray
        parameters = inspect.signature(set_tray).parameters
        # Each a PuiKit addition a pinned version may lack: degrade to what
        # it has - no image means the title glyph (macOS) or the host exe's
        # icon (Windows); no badge means the menu entry is the only sign.
        self._has_image = "image" in parameters
        self._has_badge = "badge" in parameters

    def show(self, running: tuple, unread: int) -> None:
        state = (running, unread)
        if state == self._shown:
            return
        self._shown = state

        top = [MenuItem(f"Running: {name} — Stop",
                        on_select=lambda n=name: _cancel(n))
               for name in running]
        if unread:
            top.append(MenuItem(
                f"{unread} unread error{'s' if unread != 1 else ''} — "
                f"Open Console",
                on_select=self._console.backend.show_main_window))
        menu = Menu(*top, *([SEPARATOR] if top else []), *self._items)

        tooltip = "Keyhac"
        if running:
            tooltip += f" — running {', '.join(running)}"
        if unread:
            tooltip += f" — {unread} unread error{'s' if unread != 1 else ''}"

        set_tray = self._console.backend.set_tray
        image = _tray_image(busy=bool(running)) if self._has_image else None
        options = {"tooltip": tooltip}
        if self._has_badge:
            options["badge"] = _ERROR_BADGE if unread else None
        if image is None:
            set_tray("⌨", menu, **options)
        else:
            set_tray(None, menu, image=image, **options)


def _cancel(name: str) -> None:
    """Stop the running action filed under `name` - what Esc does."""
    from keyhac.core.action import ThreadedAction

    with ThreadedAction._running_lock:
        running = tuple(ThreadedAction._running)
    for action in running:
        record = getattr(action, "_run_record", None)
        if (getattr(record, "name", None) or type(action).__name__) == name:
            flag = getattr(action, "_cancel_flag", None)
            if flag is not None:
                flag.set()
