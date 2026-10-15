"""macOS window operations - Accessibility API + CoreGraphics.

The macOS half of the portable Window / WindowProvider pair (see
keyhac/platform/base.py).  Windows are AX elements, and every one this
provider hands out belongs to another application: Keyhac's own windows do
not exist here (issue #173).  That is what makes the whole provider usable
from any thread - AX into our *own* process off the main thread crashes with
SIGTRAP, AX into another application does not.

STATUS: written to spec against the AX calls already proven in
keyhac/platform/mac/uielement.py and the previous MoveWindow implementation;
needs a live macOS pass (this session ran on Windows).
"""

import os
import time

import ApplicationServices as AS
from AppKit import NSWorkspace, NSRunningApplication

from keyhac.platform.base import Window, WindowProvider
from keyhac.platform.mac.uielement import (
    UIElement, app_name_of, focused_application, keyhac_in_front)
from keyhac.core import log

logger = log.getLogger("MacWindow")

#: Same cap the focus provider uses, so a pathological AX tree cannot hang the
#: hook thread.
AX_MESSAGING_TIMEOUT = 0.1

#: Window-menu items the OS adds to every AppKit application (macOS 15+),
#: identified by their action selectors.  Private selectors - nothing in the
#: SDK declares them - so they may change in a later macOS; when they cannot
#: be found, maximize() places the window instead and restore() reports
#: False.  There is no public API for Fill at all, neither in AppKit nor in
#: AX.  The identifier is used rather than the title because the title is
#: localized.
FILL_ITEM = "_zoomFill:"
UNTILE_ITEM = "_zoomUntile:"     # Window > Move & Resize > Return to Previous Size

#: How far inside the work area Fill puts the window when "Tiled windows have
#: margins" is on (System Settings > Desktop & Dock), measured on macOS 27.
#: Accepted rather than read: the setting is another application's
#: preference (com.apple.WindowManager EnableTiledWindowMargins), and taking
#: both frames costs only a window sized 8 px inside the work area by hand.
TILE_MARGIN = 8.0

#: How long maximize()/restore() wait for a background application to come
#: forward before pressing its menu - on the main thread, so kept short.
ACTIVATION_TIMEOUT = 0.5

#: The menu-bar read's cap.  An application coming back from the Dock is
#: busy animating for 200-400 ms and let 0.1 s reads time out
#: (kAXErrorCannotComplete); this is an explicit action, not the hook path
#: AX_MESSAGING_TIMEOUT protects.
MENU_TIMEOUT = 0.5

#: Fill rounds a fractional work area (37.5 px under a notch with the menu bar
#: hidden) to whole pixels.
FRAME_TOLERANCE = 2.0


def _screen_work_frames():
    """NSScreen.visibleFrame per screen, top-left origin, primary first."""
    from AppKit import NSScreen
    screens = NSScreen.screens()
    if not screens:
        return []
    # AppKit frames are bottom-left-origin global; the primary screen's frame
    # has origin (0, 0), so its height anchors the flip into the
    # AX/CoreGraphics top-left coordinates everything else here uses.
    primary_height = screens[0].frame().size.height
    frames = []
    for screen in screens:
        v = screen.visibleFrame()
        frames.append((float(v.origin.x),
                       float(primary_height - v.origin.y - v.size.height),
                       float(v.size.width), float(v.size.height)))
    return frames


def _best_work_frame(frame, work_frames):
    """The work area the frame overlaps most, or None."""
    x, y, w, h = frame
    best, best_area = None, 0.0
    for work in work_frames:
        sx, sy, sw, sh = work
        overlap = (max(0.0, min(x + w, sx + sw) - max(x, sx))
                   * max(0.0, min(y + h, sy + sh) - max(y, sy)))
        if overlap > best_area:
            best, best_area = work, overlap
    return best


def _fills(frame, work) -> bool:
    """Whether frame is work, or work inset by the tiling margin."""
    for inset in (0.0, TILE_MARGIN):
        target = (work[0] + inset, work[1] + inset,
                  work[2] - 2 * inset, work[3] - 2 * inset)
        if all(abs(a - b) <= FRAME_TOLERANCE for a, b in zip(frame, target)):
            return True
    return False


def _find_menu_item(menu, identifier, depth):
    """Depth-limited search of an AXMenu for an item by AXIdentifier.

    One batched read per item - every read is a round trip into the
    application.
    """
    for item in menu.children():
        values = item.get_attribute_values(["AXIdentifier", "AXChildren"])
        if values["AXIdentifier"] == identifier:
            return item
        if depth > 1:
            for submenu in values["AXChildren"] or []:
                if isinstance(submenu, UIElement):
                    found = _find_menu_item(submenu, identifier, depth - 1)
                    if found is not None:
                        return found
    return None


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

    def _set(self, name, type_name, value) -> bool:
        """An AX attribute write that reports whether AX accepted it.

        The answer matters: a full-screen window refuses AXPosition with
        kAXErrorFailure, and a write whose error is dropped reads as a move
        that happened.
        """
        try:
            return self._element.set_attribute_value(name, type_name, value)
        except Exception:
            logger.debug(f"AX write of {name} failed", exc_info=True)
            return False

    def set_frame(self, x, y, w=None, h=None) -> bool:
        # An AX frame write also ends Fill (or any other tile): the OS drops
        # the tiled state, measured on macOS 27.
        ok = self._set("AXPosition", "point", (x, y))
        if w is not None and h is not None:
            ok = self._set("AXSize", "size", (w, h)) and ok
        return ok

    # -- state --------------------------------------------------------------

    def is_minimized(self) -> bool:
        return bool(self._element.get_attribute_value("AXMinimized"))

    def is_full_screen(self) -> bool:
        return bool(self._element.get_attribute_value("AXFullScreen"))

    def is_maximized(self) -> bool:
        # AXFullScreen first: under a notch a full-screen window keeps clear
        # of the menu-bar strip, so its frame can equal the work area.
        if self.is_minimized() or self.is_full_screen():
            return False
        frame = self.get_frame()
        if frame is None:
            return False
        work = _best_work_frame(frame, _screen_work_frames())
        return work is not None and _fills(frame, work)

    def restore(self) -> bool:
        if self.is_minimized():
            return self._set("AXMinimized", "bool", False)
        if self.is_full_screen():
            return self._set("AXFullScreen", "bool", False)
        if not self.is_maximized():
            return False
        # Return to Previous Size acts on the application's main window and,
        # like Fill, does nothing while the application is in the background.
        if not self._bring_forward():
            return False
        # AXEnabled is not consulted: AppKit revalidates menu items when a
        # menu opens, so read through AX it still holds whatever it was the
        # last time the user looked - False for an application just brought
        # forward.  A press on an item that does not apply does nothing.
        item = self._window_menu_item(UNTILE_ITEM, depth=2)
        if item is None:
            logger.info(f"{self._app_name} has no Window > Move & Resize > "
                        f"Return to Previous Size ({UNTILE_ITEM}).")
            return False
        return item.perform_action("AXPress")

    def maximize(self) -> bool:
        if self.is_maximized():
            return False
        if self.is_minimized():
            self._set("AXMinimized", "bool", False)
        elif self.is_full_screen():
            # Fill is ignored until the way out of full screen has finished
            # animating - over 0.4 s, too long to hold the main thread - but
            # the window's frame is back at once, and a frame write is not
            # ignored.  A window that was filled before full screen comes back
            # filled, previous size and all, and needs nothing more.
            if not self._set("AXFullScreen", "bool", False):
                return False
            return self.is_maximized() or self._place_on_work_area()
        # A window that cannot be resized (a fixed-size panel) cannot fill;
        # its Fill item is disabled, which AX cannot be trusted to say (see
        # restore()), but its AXSize is not settable, which it can.
        err, resizable = AS.AXUIElementIsAttributeSettable(
            self._element._ref, "AXSize", None)
        if err != 0 or not resizable:
            return False
        # Fill acts on the application's main window, and a press that
        # reaches an application in the background is accepted and ignored.
        if not self._bring_forward():
            return False
        item = self._window_menu_item(FILL_ITEM, depth=1)
        if item is not None:
            return item.perform_action("AXPress")
        logger.info(f"{self._app_name} has no Window > Fill ({FILL_ITEM}); "
                    f"placing the window instead.")
        return self._place_on_work_area()

    def _place_on_work_area(self) -> bool:
        """Fill's frame without Fill: no previous size for restore()."""
        frame = self.get_frame()
        work = frame and _best_work_frame(frame, _screen_work_frames())
        if not work:
            return False
        return self.set_frame(*work)

    def enter_full_screen(self) -> bool:
        if self.is_full_screen():
            return False
        if self.is_minimized():
            self._set("AXMinimized", "bool", False)
        return self._set("AXFullScreen", "bool", True)

    def minimize(self) -> bool:
        # A full-screen window takes the AXMinimized write and stays put
        # (measured), as its minimize button is disabled.
        if self.is_minimized() or self.is_full_screen():
            return False
        return self._set("AXMinimized", "bool", True)

    def _bring_forward(self, timeout=ACTIVATION_TIMEOUT) -> bool:
        """activate(), and wait until the application is really in front.

        A menu press sent right after activating a background application
        was ignored in 2 of 6 trials, and in none of 6 sent after the
        application reported AXFrontmost - which took 24-48 ms.  An
        application already in front needs no wait: AXRaise makes the window
        main at once.
        """
        if self._pid is None:
            return False
        app = AS.AXUIElementCreateApplication(self._pid)
        AS.AXUIElementSetMessagingTimeout(app, AX_MESSAGING_TIMEOUT)
        app = UIElement(app)
        was_in_front = bool(app.get_attribute_value("AXFrontmost"))
        if not self.activate():
            return False
        if was_in_front:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if app.get_attribute_value("AXFrontmost"):
                return True
            time.sleep(0.01)
        logger.info(f"{self._app_name} did not come to the front in time.")
        return False

    def _window_menu_item(self, identifier, depth):
        """An item of the application's Window menu, by AXIdentifier.

        The Window menu sits near the end of the menu bar, so the walk runs
        from the end, and it skips the Apple menu at the front, whose Recent
        Items submenu is the largest thing in any menu bar.
        """
        if self._pid is None:
            return None
        app = AS.AXUIElementCreateApplication(self._pid)
        AS.AXUIElementSetMessagingTimeout(app, MENU_TIMEOUT)
        bar = UIElement(app).get_attribute_value("AXMenuBar")
        if not isinstance(bar, UIElement):
            return None
        for title_item in reversed(bar.children()[1:]):
            for menu in title_item.children():
                found = _find_menu_item(menu, identifier, depth)
                if found is not None:
                    return found
        return None

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

    # -- discovery (AX, never into our own process - any thread) --------------

    def get_active_window(self) -> MacWindow | None:
        """The focused window of the application the keyboard is talking to.

        Resolved by `uielement.focused_application()`, the same answer
        `MacFocusProvider.get_focus` builds a `Focus` from, rather than asking
        `NSWorkspace.frontmostApplication()` a third time: this was once seen
        naming VS Code while the operator was working in another application
        and `ui.focused()` named that other application's field (issue #45).
        The two sources have not been caught disagreeing since, but one of
        them cannot disagree with itself.
        """
        if keyhac_in_front():
            return None
        app_element, pid = focused_application(self._system_wide,
                                               timeout=AX_MESSAGING_TIMEOUT)
        if app_element is None or pid == os.getpid():
            return None
        element = UIElement(app_element).get_attribute_value("AXFocusedWindow")
        if element is None:
            element = UIElement(app_element).get_attribute_value("AXMainWindow")
        if element is None:
            return None
        return MacWindow(element, app_name_of(pid), pid)

    def list_windows(self) -> list[MacWindow]:
        """Windows of every running app that has any, grouped by application.

        Within one application the order is the z-order, front-most first:
        activating a window moves it to the head of its group.  Across
        applications there is no such guarantee - NSWorkspace does not
        document runningApplications() as activation-ordered, and it measurably
        is not (with Terminal in front the list still began with a VS Code
        window).  So this list answers "which window of that application is
        current" and not "which application is in front"; get_active_window()
        answers the second.  EnumWindows on Windows is a single global z-order
        and does answer both.
        """
        windows = []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            # Skip background-only processes: they own no windows and each one
            # costs an AX round trip to discover that.
            if app.activationPolicy() != 0:  # NSApplicationActivationPolicyRegular
                continue
            pid = int(app.processIdentifier())
            if pid == os.getpid():
                # Our own windows do not exist here (issue #173) - skipped
                # before anything is asked, since asking would be AX into
                # our own process.
                continue
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

    # -- geometry (AppKit: the loop thread, via Keymap.screen_work_frames) ----

    def screen_work_frames(self):
        """Work area per screen (menu bar and Dock excluded), primary first.

        NSScreen.visibleFrame is the only source for this - CoreGraphics has
        no Dock knowledge - so unlike the two queries above this one is
        AppKit-backed and must stay on the main thread."""
        return _screen_work_frames()
