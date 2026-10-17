"""Windows focus provider - Win32 for the window, UI Automation for the path.

The focus path is the full control hierarchy down to the focused element, the
same granularity as the macOS AX path, rendered as
``/Application(Code)/Window(title)/Pane()/.../Edit()``.  UI Automation
supplies the hierarchy: walking HWND parents cannot, because a UWP/WinUI,
Electron or Chrome window is a single HWND containing the whole UI.

COST, and why this file caches.  ``get_focus()`` is called from inside the
low-level keyboard hook on every key down *and* up.  A full UIA focus walk
measured **~33 ms** on a deep Electron tree (VS Code, 20 levels) - three
thousand times the ~0.01 ms of the Win32 probe below, and enough to risk the
silent unhook that the hook's own sanity check exists to recover from.  So the
walk runs only when something it reads has changed; between focus changes the
whole call is the probe below.

Known follow-up: UIA cache requests (``IUIAutomationCacheRequest`` +
``BuildUpdatedCache``) fetch a subtree's properties in one cross-process call
instead of one per property per level, which is the standard fix for that
33 ms and would make the walk cheap enough to stop worrying about.

THE PROBE: two HWNDs, a title and the focused element.  Two HWNDs and a title
cover ``app=``, ``title=`` and ``class_name=`` *exactly* - the same HWND is
the same process, and the focused HWND determines the class name - and they
are pure Win32: ``GetWindowTextW`` deliberately does not message a window it
does not own.  They do not cover ``focus_path_pattern=``: focus moving
*within* a window is invisible to them, which includes ListView items and tab
items in classic applications and **every** in-page focus move in Chromium,
Electron, WPF and WinUI, where there is one HWND and no focusable children at
all.  So the probe also reads ``GetFocusedElement`` on every key event and
compares it with the previous one, which is what macOS has always done with
``AXFocusedUIElement`` and ``CFEqual`` (issue #150).

What that costs (measured 2026-09-13, ``tools/win_probe_cost_pass.py``,
Windows 11 Home 10.0.26200, warm medians over 200 samples each):

===============================  ==========================================
the Win32 part, same loop        0.012 ms
``GetFocusedElement``            1.6 ms WinUI / 2.7 ms Chromium / 3.5 ms
                                 classic Win32
``CompareElements``              0.008-0.019 ms - client-local, a UIA
                                 element carries its runtime id
a foreground app pumping slowly  210 ms per read (50 ms per message)
a foreground app not pumping     250 ms, then cut short (below)
===============================  ==========================================

Issue #145 measured the same read and refused it, and the deciding row was
the last one: then, a hung foreground window never returned.  Since #171
every UIA call is capped at ``UIA_TRANSACTION_TIMEOUT_MS`` (250 ms, under
the hook's 300 ms), and a read the cap cuts short has ``get_focus`` settle
for the window-level path and reuse it for ``CUT_SHORT_FOCUS_RETRY`` without
asking again - the rule macOS follows with its AX messaging timeout.  What
is left is the median, which is a few milliseconds per key event, and an
application slow but under the cap, which costs every key event what it
costs itself: accepted, as on macOS, as that application's misbehaviour.

Why not pushed instead (issue #150, both OSes measured).  A focus event says
only *when* to re-read, never that a re-read can be skipped: on macOS the
notification trails the focus by 10-50 ms, and on Windows a key can overtake
``EVENT_OBJECT_FOCUS`` too.  Polling asks the application where its focus is
*now*, which is the only answer key dispatch can use.

Keyhac's own windows keep the old rule - the element is read only when the
handles or the title change - so typing into the console does not have the
hook ask its own process on every key.

STATUS: run on Windows - app/title/class_name and the UIA path are verified
against Win32 ground truth for the same window.
"""

import ctypes
import os
import sys
import time

from keyhac.platform.base import FocusProvider, Focus
from keyhac.core.focus import CUT_SHORT_FOCUS_RETRY, FOCUS_PATH_TRANS_TABLE
from keyhac.core import log

logger = log.getLogger("WinFocus")

#: Hang guard on the parent walk, mirroring the macOS provider's bound.
MAX_PATH_DEPTH = 64

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    # Mandatory on 64-bit: the default c_int restype truncates HWND/HANDLE.
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


class NativeWindow:
    """Minimal Win32 window wrapper exposed as Focus.native.

    Deliberately HWND-level: the semantic element tree is UIElement (see
    keyhac/platform/win/uielement.py), reachable as Focus.element.
    """

    def __init__(self, hwnd):
        self.hwnd = hwnd

    def get_text(self) -> str:
        length = user32.GetWindowTextLengthW(self.hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(self.hwnd, buf, length + 1)
        return buf.value

    def get_class_name(self) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(self.hwnd, buf, 256)
        return buf.value

    def get_process_name(self) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(handle)

    def get_pid(self) -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        return pid.value


def _component(role: str | None, name: str | None) -> str:
    role = (role or "").translate(FOCUS_PATH_TRANS_TABLE)
    name = (name or "").translate(FOCUS_PATH_TRANS_TABLE)
    return f"{role}({name})"


class WinFocusProvider(FocusProvider):

    _path_cut_short = False

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinFocusProvider requires Windows")
        self._probe = None      # last cheap Win32 probe
        self._focus = None      # the Focus built for it
        # Set by _build_path when a UIA timeout cut the path to the window
        # level - a Focus cached only briefly (see get_focus).
        self._path_cut_short = False
        self._focus_expires = None  # when a cut-short Focus is asked again
        self._element = None    # the focused UIElement self._focus was built for

    def get_focused_element(self):
        """UIA's own answer, asked fresh - no probe cache, no path walk.

        get_focus() answers with the Focus frozen at the last key event, which
        is right for key dispatch and wrong for an action asking minutes later
        (issue #44). This is the read get_focus() starts with and nothing else
        - one cross-process call, 1.6-3.5 ms warm, without the ~33 ms path
        walk an action does not need.
        """
        from keyhac.platform.win.uielement import UIElement

        try:
            return UIElement.from_focus()
        except Exception:
            logger.debug("UIA focus query failed.", exc_info=True)
            return None

    def get_focus(self) -> Focus | None:

        foreground = user32.GetForegroundWindow()
        if not foreground:
            return None

        # The actually-focused child window (GUITHREADINFO.hwndFocus) gives the
        # class name that keyhac-win configs match against (e.g. "Edit").
        focus_hwnd = foreground
        thread_id = user32.GetWindowThreadProcessId(foreground, None)
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) and info.hwndFocus:
            focus_hwnd = info.hwndFocus

        top = NativeWindow(foreground)
        focused = NativeWindow(focus_hwnd)
        title = top.get_text()
        pid = top.get_pid()

        # Two handles and a window title: pure Win32, asking no other process
        # anything, and exact for app=, title= and class_name=.
        probe = (int(foreground), int(focus_hwnd), title)

        # A path a timeout cut short is reused for CUT_SHORT_FOCUS_RETRY while
        # the same window is in front (issue #175, as on macOS). Checked
        # before the focused-element read below, because that read is served
        # by the application that just timed out, and would pay the timeout
        # again on every key down and up while it stayed hung.
        if self._focus_expires is not None:
            if probe == self._probe and time.monotonic() < self._focus_expires:
                return self._focus
            self._focus_expires = None
            self._focus = None

        # The focused element, read on every key event so that focus moving
        # *inside* one window - where none of the three handles above change
        # - re-evaluates focus_path_pattern (issue #150; the numbers and the
        # case for paying them are in this file's docstring). Keyhac's own
        # windows keep the old rule - re-read only when the cheap tier
        # changes - so typing into the console does not have the hook ask
        # its own process on every key.
        own = pid == os.getpid()
        unchanged = probe == self._probe and self._focus is not None
        if own and unchanged:
            return self._focus
        element, cut_short = self._read_focused_element()
        if not own and unchanged and not cut_short \
                and self._same_focused(element):
            return self._focus

        exe = top.get_process_name()
        app_name = exe.removesuffix(".exe").removesuffix(".EXE") if exe else None
        class_name = focused.get_class_name()
        path, element = self._build_path(app_name, title, class_name,
                                         element, cut_short)
        self._element = element
        if own:
            # Matched on as usual, but no element handed out: Keyhac's own
            # windows do not exist to the element API (issue #173).
            element = None

        focus = Focus(
            app_name=app_name,
            pid=pid,
            window_title=title,
            class_name=class_name,
            path=path,
            native=focused,
            element=element,
        )
        # A cut-short path is cached for CUT_SHORT_FOCUS_RETRY only, so an
        # application that recovers gets its full path back within it.
        self._probe = probe
        self._focus = focus
        self._focus_expires = (time.monotonic() + CUT_SHORT_FOCUS_RETRY
                               if self._path_cut_short else None)
        return focus

    @staticmethod
    def _read_focused_element():
        """(UIElement or None, whether a UIA timeout cut the read short)."""
        from keyhac.core.uitree import Unresponsive
        from keyhac.platform.win.uielement import UIElement

        try:
            return UIElement.from_focus(), False
        except Unresponsive:
            logger.debug("UIA timed out asking for the focus; using the "
                         "window-level path.")
            return None, True
        except Exception:
            logger.debug("UIA focus query failed; falling back to window path.",
                         exc_info=True)
            return None, False

    def _same_focused(self, element) -> bool:
        """Whether *element* is the one the cached Focus was built for.

        `CompareElements` on two fetched elements is client-local, 0.008-0.019
        ms measured. It stops being local when the cached one belongs to a
        process that has exited (2.35 ms, and "cannot say"), and "cannot say"
        is read as a change: rebuilding is always correct, reusing is not.
        """
        from keyhac.platform.win.uielement import _same_element

        previous = self._element
        if element is None or previous is None:
            return element is None and previous is None
        return _same_element(previous._ptr, element._ptr) is True

    # ------------------------------------------------------------------

    def _build_path(self, app_name: str | None, title: str, class_name: str,
                    element, cut_short: bool):
        """(path, focused UIElement). The UIA control hierarchy from the
        application down to *element* - the focused element get_focus has
        already read - or the window-level path when there is none."""
        from keyhac.core.uitree import Unresponsive

        # Same shape, one level deep - patterns written against the full
        # path still parse, they just cannot match below the window.
        window_path = "/" + "/".join((_component("Application", app_name),
                                      _component(class_name, title)))

        self._path_cut_short = cut_short
        if element is None:
            return window_path, None

        chain = []
        elm = element
        try:
            for _ in range(MAX_PATH_DEPTH):
                if elm is None:
                    break
                control_type = elm.get_attribute_value("ControlType")
                # The desktop root is every app's parent and says nothing;
                # stop there so the path is rooted at the application, like
                # macOS's AXApplication.
                if control_type == "Pane" and \
                        elm.get_attribute_value("ClassName") == "#32769":
                    break
                chain.append((control_type, elm.get_attribute_value("Name")))
                elm = elm.parent()
        except Unresponsive:
            # Issue #171: GetFocusedElement answered and the application
            # stopped answering after it. The exception has already ended the
            # walk at the first time limit; report the window level - a
            # half-read chain would be a path that matches nothing.
            logger.debug("UIA timed out building the focus path; using the "
                         "window-level path.")
            self._path_cut_short = True
            return window_path, None

        components = [""] + [_component("Application", app_name)]
        for control_type, name in reversed(chain):
            components.append(_component(control_type, name))
        return "/".join(components), element
