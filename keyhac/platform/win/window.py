"""Windows window operations - Win32 via ctypes.

The portable half of what keyhac-win configs got from pyauto.Window: find,
enumerate, activate, restore, move.  Deliberately HWND-based, not UI
Automation: window-level operations are exactly what HWNDs are good at, and
they work on every app regardless of accessibility support.  The semantic
element tree is the other module (uielement.py).

STATUS: run on Windows - enumeration, activation and frame get/set verified
against live windows.
"""

import ctypes
import os
import sys

from keyhac.platform.base import Window, WindowProvider
from keyhac.core import log

logger = log.getLogger("WinWindow")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SW_MINIMIZE = 6
    SW_RESTORE = 9
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    GA_ROOT = 2
    # A resize border is ~7px at 100% DPI and scales with it; anything past
    # this is not a border, it is a window whose two rects disagree for some
    # other reason (minimized, or a shell surface with no frame at all).
    MAX_FRAME_INSET = 64
    MONITORINFOF_PRIMARY = 0x1

    ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    # Explicit prototypes: mandatory on 64-bit, where the default c_int restype
    # truncates HWND/HANDLE.
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetLastActivePopup.argtypes = [wintypes.HWND]
    user32.GetLastActivePopup.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [ENUMWINDOWSPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p,
                                           MONITORENUMPROC, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    user32.GetShellWindow.argtypes = []
    user32.GetShellWindow.restype = wintypes.HWND

    # DWM cloaking: a suspended UWP app keeps a visible top-level HWND that is
    # not on screen at all, so IsWindowVisible alone over-reports on Windows 10+.
    DWMWA_CLOAKED = 14
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    except OSError:  # pre-Vista / DWM unavailable: nothing is ever cloaked
        dwmapi = None


def _is_cloaked(hwnd) -> bool:
    if dwmapi is None:
        return False
    cloaked = wintypes.DWORD()
    hr = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return hr == 0 and bool(cloaked.value)


class WinWindow(Window):
    """A top-level HWND."""

    def __init__(self, hwnd):
        self.hwnd = hwnd

    def __repr__(self):
        return f'WinWindow({self.class_name}, "{self.title}")'

    def __eq__(self, other):
        return isinstance(other, WinWindow) and int(self.hwnd) == int(other.hwnd)

    def __hash__(self):
        return hash(int(self.hwnd))

    # -- identity -----------------------------------------------------------

    @property
    def element(self):
        """lazydocs: ignore"""
        from keyhac.platform.win.uielement import UIElement
        return UIElement.from_hwnd(self.hwnd)

    @property
    def title(self) -> str | None:
        length = user32.GetWindowTextLengthW(self.hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(self.hwnd, buf, length + 1)
        return buf.value

    @property
    def class_name(self) -> str | None:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(self.hwnd, buf, 256)
        return buf.value

    @property
    def pid(self) -> int | None:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        return pid.value or None

    @property
    def app_name(self) -> str | None:
        pid = self.pid
        if not pid:
            return None
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            exe = os.path.basename(buf.value)
            return exe.removesuffix(".exe").removesuffix(".EXE")
        finally:
            kernel32.CloseHandle(handle)

    @property
    def native(self):
        return self

    # -- geometry -----------------------------------------------------------

    # The frame this class reads and writes is the window as drawn.
    # GetWindowRect/SetWindowPos work in the *window* rect, which since Vista
    # includes the invisible resize border DWM keeps outside the visible edge
    # - about 7px left, right and bottom at 100% DPI, 0 at the top.  The OS
    # positions everything of its own (its snap included) in visible
    # coordinates, so arithmetic done in window coordinates lands short:
    # a half-screen tile leaves a 7px gap at the screen edge and a 14px one
    # between two tiles.  Both accessors convert, so callers see one
    # coordinate system and it is the one the user can see.

    def _frame_insets(self):
        """(left, top, right, bottom) thickness of the invisible border.

        Zero when DWM cannot answer, and zero rather than nonsense for a
        minimized window, whose window rect is off in the -32000 corner
        while DWM still reports where the window was: the sanity bound below
        rejects the difference.
        """
        if dwmapi is None:
            return (0, 0, 0, 0)
        window = wintypes.RECT()
        visible = wintypes.RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(window)):
            return (0, 0, 0, 0)
        hr = dwmapi.DwmGetWindowAttribute(
            self.hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(visible), ctypes.sizeof(visible))
        if hr != 0:
            return (0, 0, 0, 0)
        insets = (visible.left - window.left, visible.top - window.top,
                  window.right - visible.right, window.bottom - visible.bottom)
        if not all(0 <= inset <= MAX_FRAME_INSET for inset in insets):
            return (0, 0, 0, 0)
        return insets

    def get_frame(self):
        rect = wintypes.RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return None
        left, top, right, bottom = self._frame_insets()
        return (float(rect.left + left), float(rect.top + top),
                float(rect.right - right - rect.left - left),
                float(rect.bottom - bottom - rect.top - top))

    def set_frame(self, x, y, w=None, h=None) -> bool:
        left, top, right, bottom = self._frame_insets()
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        if w is None or h is None:
            flags |= SWP_NOSIZE
            w = h = 0
        else:
            w = int(w) + left + right
            h = int(h) + top + bottom
        return bool(user32.SetWindowPos(self.hwnd, None,
                                        int(x) - left, int(y) - top,
                                        int(w), int(h), flags))

    # -- state --------------------------------------------------------------

    def is_minimized(self) -> bool:
        return bool(user32.IsIconic(self.hwnd))

    def restore(self) -> bool:
        return bool(user32.ShowWindow(self.hwnd, SW_RESTORE))

    def minimize(self) -> bool:
        return bool(user32.ShowWindow(self.hwnd, SW_MINIMIZE))

    def activate(self) -> bool:
        """Foreground this window, restoring it first if minimized.

        Windows refuses SetForegroundWindow from a process that does not own
        the foreground (the foreground lock).  keyhac-win's workaround,
        extended: attach our input queue to BOTH the current foreground
        thread and the target window's thread before the call.  Attaching to
        the foreground thread alone - the classic recipe, and what this
        method originally did - is no longer honored on current Windows 11
        when the lock is armed (the foreground app actively receiving
        input); the dual attach is, verified empirically against a
        real armed lock (see doc/dev/testing.md).  getLastActivePopup
        mirrors pyauto's behavior of raising the dialog a window currently
        owns rather than the frame behind it.
        """
        hwnd = self.hwnd
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        popup = user32.GetLastActivePopup(hwnd)
        if popup and user32.IsWindowVisible(popup):
            hwnd = popup

        if user32.SetForegroundWindow(hwnd):
            return True

        this_thread = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        fg_thread = (user32.GetWindowThreadProcessId(foreground, None)
                     if foreground else 0)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached = []
        for tid in {fg_thread, target_thread}:
            if (tid and tid != this_thread
                    and user32.AttachThreadInput(this_thread, tid, True)):
                attached.append(tid)
        try:
            ok = bool(user32.SetForegroundWindow(hwnd))
            user32.BringWindowToTop(hwnd)
        finally:
            for tid in attached:
                user32.AttachThreadInput(this_thread, tid, False)
        return ok or user32.GetForegroundWindow() == hwnd


class WinWindowProvider(WindowProvider):

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinWindowProvider requires Windows")

    def get_active_window(self) -> WinWindow | None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        # Normalize to the top-level frame: the foreground window is already
        # top-level, but a caller may pass a child through fromHWND-style code.
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        return WinWindow(root)

    def list_windows(self) -> list[WinWindow]:
        """Visible top-level windows that have a title, in Z-order (front
        first) - EnumWindows enumerates in Z-order by definition.

        Two windows that pass IsWindowVisible but are never what a caller
        means are excluded: the shell's desktop window (Progman - owned by
        explorer.exe, so `find_window(app="explorer")` would otherwise return
        the desktop instead of a File Explorer window), and DWM-cloaked
        windows, which every suspended UWP app leaves behind.
        """
        windows = []
        shell = user32.GetShellWindow()

        def _callback(hwnd, _lparam):
            if (user32.IsWindowVisible(hwnd)
                    and user32.GetWindowTextLengthW(hwnd) > 0
                    and hwnd != shell
                    and not _is_cloaked(hwnd)):
                windows.append(WinWindow(hwnd))
            return True

        # The callback object must stay referenced for the duration of the
        # call; a local does that (a bare inline WINFUNCTYPE(...) would not).
        proc = ENUMWINDOWSPROC(_callback)
        user32.EnumWindows(proc, 0)
        return windows

    def screen_frames(self):
        return self._monitor_frames("rcMonitor")

    def screen_work_frames(self):
        """Work area per monitor (taskbar excluded), primary first.

        Verified live on Windows (tests/test_win_window.py TestWorkFrames /
        TestSnapLive).
        """
        return self._monitor_frames("rcWork")

    @staticmethod
    def _monitor_frames(rect_field: str):
        results = []

        def _callback(hmonitor, _hdc, _rect, _lparam):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                m = getattr(info, rect_field)
                results.append(((float(m.left), float(m.top),
                                 float(m.right - m.left), float(m.bottom - m.top)),
                                bool(info.dwFlags & MONITORINFOF_PRIMARY)))
            return True

        proc = MONITORENUMPROC(_callback)
        user32.EnumDisplayMonitors(None, None, proc, 0)
        results.sort(key=lambda entry: not entry[1])  # primary first
        return [frame for frame, _primary in results]

    def window_frames(self):
        """Frames of on-screen top-level windows. Genuinely thread-safe.

        Enumerates separately from list_windows() rather than reusing it,
        because reading a window's *text* is a blocking SendMessage(WM_GETTEXT)
        to the owning thread: a worker calling it deadlocks against any UI
        thread that is not pumping - including our own - and hangs outright on
        another app that is wedged. GetWindowRect / IsWindowVisible / IsIconic
        are answered by the window manager without messaging the owner, so this
        path never blocks.
        """
        frames = []
        shell = user32.GetShellWindow()

        def _callback(hwnd, _lparam):
            if (user32.IsWindowVisible(hwnd) and hwnd != shell
                    and not user32.IsIconic(hwnd) and not _is_cloaked(hwnd)):
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    frames.append((float(rect.left), float(rect.top),
                                   float(rect.right - rect.left),
                                   float(rect.bottom - rect.top)))
            return True

        proc = ENUMWINDOWSPROC(_callback)
        user32.EnumWindows(proc, 0)
        return frames
