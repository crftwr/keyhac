"""macOS window operations - Accessibility API + CoreGraphics.

The macOS half of the portable Window / WindowProvider pair (see
keyhac/platform/base.py).  Windows are AX elements; the geometry queries that
a ThreadedAction worker may call use CoreGraphics instead, because AppKit and
AX are main-thread only here (AX into our *own* process off the main thread
crashes with SIGTRAP - the rule MoveWindow's thread contract came from).

STATUS: written to spec against the AX calls already proven in
keyhac/platform/mac/uielement.py and the previous MoveWindow implementation;
needs a live macOS pass (this session ran on Windows).
"""

import ApplicationServices as AS
from AppKit import NSWorkspace, NSRunningApplication

from keyhac.platform.base import Window, WindowProvider
from keyhac.platform.mac.uielement import UIElement
from keyhac.core import log

logger = log.getLogger("MacWindow")

#: Same cap the focus provider uses, so a pathological AX tree cannot hang the
#: hook thread.
AX_MESSAGING_TIMEOUT = 0.1


class MacWindow(Window):
    """An AXWindow element plus the application it belongs to."""

    def __init__(self, element: UIElement, app_name: str = None, pid: int = None):
        self._element = element
        self._app_name = app_name
        self._pid = pid

    def __repr__(self):
        return f'MacWindow({self._app_name}, "{self.title}")'

    def __eq__(self, other):
        return (isinstance(other, MacWindow)
                and self._pid == other._pid
                and self.title == other.title)

    def __hash__(self):
        return hash((self._pid, self.title))

    # -- identity -----------------------------------------------------------

    @property
    def element(self):
        """lazydocs: ignore"""
        return self._element

    @property
    def title(self) -> str | None:
        return self._element.get_attribute_value("AXTitle")

    @property
    def app_name(self) -> str | None:
        return self._app_name

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def class_name(self) -> str | None:
        return None  # Win32 concept; no macOS equivalent

    @property
    def native(self):
        return self._element

    # -- geometry -----------------------------------------------------------

    def get_frame(self):
        frame = self._element.get_attribute_value("AXFrame")
        if frame is not None:
            return tuple(frame)
        # Not every app exposes AXFrame; position + size always work.
        position = self._element.get_attribute_value("AXPosition")
        size = self._element.get_attribute_value("AXSize")
        if position is None or size is None:
            return None
        return (position[0], position[1], size[0], size[1])

    def set_frame(self, x, y, w=None, h=None) -> bool:
        try:
            self._element.set_attribute_value("AXPosition", "point", (x, y))
            if w is not None and h is not None:
                self._element.set_attribute_value("AXSize", "size", (w, h))
            return True
        except Exception:
            logger.debug("AX frame write failed", exc_info=True)
            return False

    # -- state --------------------------------------------------------------

    def is_minimized(self) -> bool:
        return bool(self._element.get_attribute_value("AXMinimized"))

    def restore(self) -> bool:
        try:
            self._element.set_attribute_value("AXMinimized", "bool", False)
            return True
        except Exception:
            return False

    def minimize(self) -> bool:
        try:
            self._element.set_attribute_value("AXMinimized", "bool", True)
            return True
        except Exception:
            return False

    def activate(self) -> bool:
        """Raise this window and bring its application forward.

        AXRaise alone orders the window within its app; the app itself still
        has to be activated.  That is done by writing AXFrontmost on the
        application element - the same call keyhac-mac shipped with - because
        since macOS 14 the polite route (NSRunningApplication
        activateWithOptions:) is a *cooperative* request that the system
        ignores when the caller is not the active app, which Keyhac usually
        is not.  A trusted process's AX write is honored unconditionally.
        """
        if self._pid is None:
            return False
        if self.is_minimized():
            self.restore()
        self._element.perform_action("AXRaise")
        app_element = AS.AXUIElementCreateApplication(self._pid)
        AS.AXUIElementSetMessagingTimeout(app_element, AX_MESSAGING_TIMEOUT)
        if AS.AXUIElementSetAttributeValue(app_element, "AXFrontmost", True) == 0:
            return True
        # Fall back to the cooperative request (e.g. AXFrontmost refused).
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self._pid)
        if app is None:
            return False
        # 1 << 1 == NSApplicationActivateIgnoringOtherApps
        return bool(app.activateWithOptions_(1 << 1))


class MacWindowProvider(WindowProvider):

    def __init__(self):
        self._system_wide = AS.AXUIElementCreateSystemWide()
        AS.AXUIElementSetMessagingTimeout(self._system_wide, AX_MESSAGING_TIMEOUT)

    # -- discovery (UI thread only: AX) --------------------------------------

    def get_active_window(self) -> MacWindow | None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        app_element = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(app_element, AX_MESSAGING_TIMEOUT)
        element = UIElement(app_element).get_attribute_value("AXFocusedWindow")
        if element is None:
            element = UIElement(app_element).get_attribute_value("AXMainWindow")
        if element is None:
            return None
        return MacWindow(element, str(app.localizedName()), pid)

    def list_windows(self) -> list[MacWindow]:
        """Windows of every running app that has any, front-most app first.

        NSWorkspace already returns applications in activation order, so the
        front-most app's windows come first - the same ordering guarantee
        EnumWindows gives on Windows.
        """
        windows = []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            # Skip background-only processes: they own no windows and each one
            # costs an AX round trip to discover that.
            if app.activationPolicy() != 0:  # NSApplicationActivationPolicyRegular
                continue
            pid = int(app.processIdentifier())
            app_element = AS.AXUIElementCreateApplication(pid)
            AS.AXUIElementSetMessagingTimeout(app_element, AX_MESSAGING_TIMEOUT)
            elements = UIElement(app_element).get_attribute_value("AXWindows") or []
            app_name = str(app.localizedName())
            for element in elements:
                if isinstance(element, UIElement):
                    windows.append(MacWindow(element, app_name, pid))
        return windows

    # -- geometry (thread-safe: CoreGraphics, never AppKit/AX) ---------------

    def screen_frames(self):
        return UIElement.get_screen_frames()

    def window_frames(self):
        return UIElement.get_onscreen_window_frames()

    # -- geometry (UI thread only: AppKit) ------------------------------------

    def screen_work_frames(self):
        """Work area per screen (menu bar and Dock excluded), primary first.

        NSScreen.visibleFrame is the only source for this - CoreGraphics has
        no Dock knowledge - so unlike the two queries above this one is
        AppKit-backed and must stay on the main thread."""
        from AppKit import NSScreen
        screens = NSScreen.screens()
        if not screens:
            return []
        # AppKit frames are bottom-left-origin global; the primary screen's
        # frame has origin (0, 0), so its height anchors the flip into the
        # AX/CoreGraphics top-left coordinates everything else here uses.
        primary_height = screens[0].frame().size.height
        frames = []
        for screen in screens:
            v = screen.visibleFrame()
            frames.append((float(v.origin.x),
                           float(primary_height - v.origin.y - v.size.height),
                           float(v.size.width), float(v.size.height)))
        return frames
