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
Win32 probe runs every time and the UIA walk only when the probe changes;
between focus changes the whole call is a dict comparison.

Known follow-up: UIA cache requests (``IUIAutomationCacheRequest`` +
``BuildUpdatedCache``) fetch a subtree's properties in one cross-process call
instead of one per property per level, which is the standard fix for that
33 ms and would make the walk cheap enough to stop worrying about.

STATUS: run on Windows - app/title/class_name and the UIA path are verified
against Win32 ground truth for the same window.
"""

import ctypes
import os
import sys

from keyhac.platform.base import FocusProvider, Focus
from keyhac.core.focus import FOCUS_PATH_TRANS_TABLE
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

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinFocusProvider requires Windows")
        self._probe = None      # last cheap Win32 probe
        self._focus = None      # the Focus built for it

    def get_focused_element(self):
        """UIA's own answer, asked fresh - no probe cache, no path walk.

        get_focus() answers out of `self._focus` while the foreground window,
        the focused child and the title are unchanged, which is right for key
        dispatch and wrong for an action: focus moves *within* a window all the
        time, and the probe cannot see it (issue #44). This is the ~33 ms walk's
        first level and nothing else.
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

        # The whole cheap tier: two handles and a window title. Everything
        # below - the process name query and the UIA walk - is skipped while
        # these are unchanged, which is the common case (every keystroke
        # typed into one window).
        probe = (int(foreground), int(focus_hwnd), title)
        if probe == self._probe and self._focus is not None:
            return self._focus

        exe = top.get_process_name()
        app_name = exe.removesuffix(".exe").removesuffix(".EXE") if exe else None
        class_name = focused.get_class_name()
        path, element = self._build_path(app_name, title, class_name)

        focus = Focus(
            app_name=app_name,
            pid=top.get_pid(),
            window_title=title,
            class_name=class_name,
            path=path,
            native=focused,
            element=element,
        )
        self._probe = probe
        self._focus = focus
        return focus

    # ------------------------------------------------------------------

    def _build_path(self, app_name: str | None, title: str, class_name: str):
        """(path, focused UIElement). The UIA control hierarchy from the
        application down to the focused element, or the window-level path when
        UI Automation is unavailable."""
        from keyhac.platform.win.uielement import UIElement

        element = None
        try:
            element = UIElement.from_focus()
        except Exception:
            logger.debug("UIA focus query failed; falling back to window path.",
                         exc_info=True)

        if element is None:
            # Same shape, one level deep - patterns written against the full
            # path still parse, they just cannot match below the window.
            return "/" + "/".join((_component("Application", app_name),
                                   _component(class_name, title))), None

        chain = []
        elm = element
        for _ in range(MAX_PATH_DEPTH):
            if elm is None:
                break
            control_type = elm.get_attribute_value("ControlType")
            # The desktop root is every app's parent and says nothing; stop
            # there so the path is rooted at the application, like macOS's
            # AXApplication.
            if control_type == "Pane" and elm.get_attribute_value("ClassName") == "#32769":
                break
            chain.append((control_type, elm.get_attribute_value("Name")))
            elm = elm.parent()

        components = [""] + [_component("Application", app_name)]
        for control_type, name in reversed(chain):
            components.append(_component(control_type, name))
        return "/".join(components), element
