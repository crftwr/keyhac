"""macOS focus provider - Accessibility API via PyObjC.

Ported from keyhac-mac: KeyhacCore_UIElement.swift (focused element lookup)
and keyhac_focus.py (focus path string construction).
"""

import os
import time

import ApplicationServices as AS
from AppKit import NSWorkspace

from keyhac.platform.base import FocusProvider, Focus
from keyhac.core.uitree import Unresponsive
from keyhac.platform.mac.uielement import (
    AX_MESSAGING_TIMEOUT, UIElement, _ax_get, app_name_of,
    focused_application, focused_element)
from keyhac.core.focus import CUT_SHORT_FOCUS_RETRY, FOCUS_PATH_TRANS_TABLE
from keyhac.core import log

logger = log.getLogger("MacFocus")

# A hung app must not stall key dispatch. Set on the system-wide element
# below, AX_MESSAGING_TIMEOUT is the default for every AX element in the
# process - not only the reads made through that element (measured, issue
# #175) - and a read it cuts short raises Unresponsive, which get_focus
# catches at the first one.


def _front_pid() -> int | None:
    """The front application's pid from NSWorkspace - asking it nothing."""
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(front.processIdentifier()) if front is not None else None


def _same_probe(probe, previous) -> bool:
    """Whether two cheap-tier probes describe the same place.

    Element refs need CFEqual rather than `==`: an AXUIElementRef is a
    CoreFoundation type whose identity is pid plus opaque element data, and
    comparing it is local - no round trip, and safe on a ref whose application
    has since quit.  The same comparison `UIElement.has_focus()` makes.
    """
    if previous is None:
        return False

    pid, element, title = probe
    old_pid, old_element, old_title = previous

    if pid != old_pid or title != old_title:
        return False
    if element is None or old_element is None:
        return element is None and old_element is None
    try:
        return bool(AS.CFEqual(element, old_element))
    except Exception:
        return False


class MacFocusProvider(FocusProvider):

    def __init__(self):
        self._system_wide = AS.AXUIElementCreateSystemWide()
        AS.AXUIElementSetMessagingTimeout(self._system_wide, AX_MESSAGING_TIMEOUT)
        self._probe = None      # last cheap AX probe - see get_focus()
        self._focus = None      # the Focus built for it
        # (pid, Focus, expiry) for a Focus a timeout cut short - see get_focus
        self._cut_short = None

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
        """The focus as key dispatch needs it, answered from the cheap tier
        where nothing has moved.

        **Why there is a cheap tier at all.** `_build_path` walks AXParent to
        the application reading AXRole and AXTitle at every level: 81 of the 83
        accessibility reads one call of this used to cost, measured against VS
        Code, and `_check_focus_change` runs it on key down *and* key up, so a
        keystroke cost 166. The milliseconds were never the argument - warm,
        that is about 2.5 ms. What matters is that every one of those reads is
        answered by the **focused application's** main thread, inside the event
        tap callback. A front application busy for 100 ms made Keyhac busy for
        100 ms, which is what `hook.py`'s `kCGEventTapDisabledByTimeout`
        handling exists to recover from (issue #144).

        The probe is four reads and it is what Windows has had since it was
        written (`win/focus.py`): answer out of the last result while the cheap
        tier is unchanged, which is every keystroke typed into one place.

        **Why these three components.** The pid catches an application switch.
        The focused element catches focus moving *inside* one application -
        the thing `GUITHREADINFO.hwndFocus` cannot see on Windows, so this is
        the more accurate of the two probes, not a weaker port (#145). The
        window title catches a document or tab change under a focus that never
        moved, which `title=` matches on.

        The title is read here only to *compare*: it never reaches the `Focus`.
        `window_title` keeps coming out of the path walk, transliterated
        through FOCUS_PATH_TRANS_TABLE the way every `title=` pattern is
        written against (`platform/base.py`), and a raw title used as a value
        would silently break every pattern containing a bracket.

        What the probe still cannot see is an *ancestor's* title changing while
        the focused element and the window title both stay put, which moves the
        path under a `focus_path_pattern`. That is narrower than the gap
        Windows has shipped with for as long as it has had a probe.
        """
        # A Focus a timeout cut short is reused for CUT_SHORT_FOCUS_RETRY while
        # the same application is in front (issue #175). Checked before any
        # AX read, because the first of them - AXFocusedApplication, through
        # the system-wide element - is served by the front application too,
        # and would pay the timeout again on every key event; NSWorkspace
        # asks it nothing.
        if self._cut_short is not None:
            pid, focus, expires = self._cut_short
            if time.monotonic() < expires and _front_pid() == pid:
                return focus
            self._cut_short = None

        focused_app, pid = focused_application(self._system_wide)

        if focused_app is None:
            self._probe = None
            self._focus = None
            app_name = app_name_of(pid)
            return Focus(app_name=app_name, pid=pid) if app_name else None

        app_name = app_name_of(pid)
        try:
            # Not named focused_element: that is the module-level resolution
            # this file imports, and shadowing it is how the two get confused.
            control = _ax_get(focused_app, "AXFocusedUIElement")
            window = _ax_get(focused_app, "AXFocusedWindow")
            title = _ax_get(window, "AXTitle") if window is not None else None

            probe = (pid, control, str(title) if title is not None else None)
            if self._focus is not None and _same_probe(probe, self._probe):
                return self._focus

            # Both reads are already in hand, so the fallback chain costs
            # nothing beyond them: a focused control if there is one, else
            # the window it would have been in, else the application itself
            # - a key table can still match on *something* when the control
            # cannot be read.
            element = control if control is not None else window
            if element is None:
                element = focused_app

            path, window_title = self._build_path(element)
        except Unresponsive:
            # The application stopped answering (issue #175): stop at the
            # first timeout and match on the application alone. The reads
            # used to carry on - six on a rebuild, 610 ms measured, and three
            # on every later key event - and ended in a path of '/()', which
            # matched nothing. Reused for CUT_SHORT_FOCUS_RETRY only, so an
            # application that recovers gets its full path back within it.
            self._probe = None
            self._focus = None
            element = None if pid == os.getpid() else UIElement(focused_app)
            path = "/AXApplication({})".format(
                (app_name or "").translate(FOCUS_PATH_TRANS_TABLE))
            focus = Focus(app_name=app_name, pid=pid, path=path,
                          native=element, element=element)
            self._cut_short = (pid, focus,
                               time.monotonic() + CUT_SHORT_FOCUS_RETRY)
            return focus

        # native and element are the same object here: on macOS the focused
        # semantic element *is* the native handle. They diverge on Windows,
        # where native is an HWND wrapper and element is a UIA element.
        ui_element = UIElement(element)
        # Keyhac in front keeps everything key dispatch matches on - the
        # name, the title, the path - but hands out no element (issue #173):
        # our own windows do not exist to the element API, and an action
        # reading this one from its worker would be AX into our own process.
        if pid == os.getpid():
            ui_element = None
        focus = Focus(
            app_name=app_name,
            pid=pid,
            window_title=window_title,
            class_name=None,
            path=path,
            native=ui_element,
            element=ui_element,
        )
        self._probe = probe
        self._focus = focus
        return focus

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
