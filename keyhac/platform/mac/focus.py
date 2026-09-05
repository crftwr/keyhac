"""macOS focus provider - Accessibility API via PyObjC.

Ported from keyhac-mac: KeyhacCore_UIElement.swift (focused element lookup)
and keyhac_focus.py (focus path string construction).
"""

from AppKit import NSWorkspace
import ApplicationServices as AS

from keyhac.platform.base import FocusProvider, Focus
from keyhac.platform.mac.uielement import UIElement, _ax_get, focused_element
from keyhac.core.focus import FOCUS_PATH_TRANS_TABLE
from keyhac.core import log

logger = log.getLogger("MacFocus")

# A hung app must not stall key dispatch: cap AX IPC waiting time (seconds).
AX_MESSAGING_TIMEOUT = 0.1


class MacFocusProvider(FocusProvider):

    def __init__(self):
        self._system_wide = AS.AXUIElementCreateSystemWide()
        AS.AXUIElementSetMessagingTimeout(self._system_wide, AX_MESSAGING_TIMEOUT)

    def get_focused_element(self) -> "UIElement | None":
        """Ask the system where focus is, without building a focus path.

        The same AX chain get_focus() walks, stopping at the element. Skipping
        `_build_path` is worth a method of its own here: that walk is up to 64
        levels of AXParent with two attribute reads each, all of it cross-
        process, and an action polling for focus to settle pays it on every
        turn while reading none of it.

        No `AXFocusedWindow` fallback and no app element, unlike get_focus():
        those exist so a key table can still match on *something* when the
        focused control cannot be read, and handing an action the application
        element as if it were the focus is how issue #44 read on screen. Here,
        not knowing is an answer worth giving.

        The resolution itself lives in `uielement.focused_element()`, shared
        with the focus predicates on UIElement: they were written separately
        and one of them was written without the fallback below, which is the
        whole reason it is one function now. The timed system-wide element is
        passed in so that sharing does not cost this path its timeout.
        """
        return focused_element(self._system_wide)

    def get_focus(self) -> Focus | None:

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = str(app.localizedName()) if app else None
        pid = int(app.processIdentifier()) if app else None

        focused_app = _ax_get(self._system_wide, "AXFocusedApplication")
        if focused_app is None and pid is not None:
            focused_app = AS.AXUIElementCreateApplication(pid)

        element = None
        if focused_app is not None:
            element = _ax_get(focused_app, "AXFocusedUIElement")
            if element is None:
                element = _ax_get(focused_app, "AXFocusedWindow")
            if element is None:
                element = focused_app

        if element is None:
            if app_name is None:
                return None
            return Focus(app_name=app_name, pid=pid)

        path, window_title = self._build_path(element)

        # native and element are the same object here: on macOS the focused
        # semantic element *is* the native handle. They diverge on Windows,
        # where native is an HWND wrapper and element is a UIA element.
        ui_element = UIElement(element)
        return Focus(
            app_name=app_name,
            pid=pid,
            window_title=window_title,
            class_name=None,
            path=path,
            native=ui_element,
            element=ui_element,
        )

    @staticmethod
    def _build_path(element):
        """Walk AXParent to the application and render each level as
        AXRole(AXTitle) - identical to keyhac-mac focus paths."""

        chain = []
        elm = element
        # Bounded walk as a hang guard against pathological AX trees
        for _ in range(64):
            if elm is None:
                break
            chain.append(elm)
            elm = _ax_get(elm, "AXParent")

        components = [""]
        window_title = None

        for elm in reversed(chain):
            role = _ax_get(elm, "AXRole") or ""
            title = _ax_get(elm, "AXTitle") or ""
            role = str(role).translate(FOCUS_PATH_TRANS_TABLE)
            title = str(title).translate(FOCUS_PATH_TRANS_TABLE)
            if window_title is None and role == "AXWindow":
                window_title = title
            components.append(f"{role}({title})")

        return "/".join(components), window_title
