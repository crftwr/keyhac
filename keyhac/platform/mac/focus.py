"""macOS focus provider - Accessibility API via PyObjC.

Ported from keyhac-mac: KeyhacCore_UIElement.swift (focused element lookup)
and keyhac_focus.py (focus path string construction).
"""

from AppKit import NSWorkspace
import ApplicationServices as AS

from keyhac.platform.base import FocusProvider, Focus
from keyhac.core.focus import FOCUS_PATH_TRANS_TABLE
from keyhac.core import log

logger = log.getLogger("MacFocus")

# A hung app must not stall key dispatch: cap AX IPC waiting time (seconds).
AX_MESSAGING_TIMEOUT = 0.1


def _ax_get(element, attribute):
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    if err != 0:
        return None
    return value


class MacFocusProvider(FocusProvider):

    def __init__(self):
        self._system_wide = AS.AXUIElementCreateSystemWide()
        AS.AXUIElementSetMessagingTimeout(self._system_wide, AX_MESSAGING_TIMEOUT)

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

        return Focus(
            app_name=app_name,
            pid=pid,
            window_title=window_title,
            class_name=None,
            path=path,
            native=element,
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
