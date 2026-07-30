"""Single-instance guard - a named mutex, plus surfacing the running instance.

Two Keyhac processes would each install a WH_KEYBOARD_LL hook and both would
act on (and possibly re-inject) every key, so a second launch must not get as
far as installing one.  The guard is a named kernel mutex: created without the
``Global\\`` prefix it lives in the caller's session namespace, so one Keyhac
per logged-in session - exactly the scope of a per-session keyboard hook.
The OS releases it however the process dies; there is no stale-lock case.

The typical second launch is a double-click on Keyhac.exe by a user who forgot
it is already in the tray (its console may be hidden), so the guard also gives
feedback: it re-shows the running instance's console window if it can find it,
else falls back to a message box.
"""

import ctypes
import sys

from keyhac.core import log

logger = log.getLogger("WinInstance")

#: Session-local kernel object name (no Global\\ prefix on purpose).
_MUTEX_NAME = "crftwr.Keyhac2.SingleInstance"

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Mandatory on 64-bit: the default c_int restype truncates HWND/HANDLE.
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL,
                                      wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.UINT)
    user32.MessageBoxW.restype = ctypes.c_int

    ERROR_ALREADY_EXISTS = 183
    SW_SHOW = 5
    MB_ICONINFORMATION = 0x40


class InstanceLock:
    """Holds the mutex handle for the process lifetime; the OS releases it on
    exit.  release() exists for tests."""

    def __init__(self, handle):
        self._handle = handle

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


def acquire_instance_lock(name: str = _MUTEX_NAME):
    """Try to become this session's Keyhac instance.  Returns an InstanceLock
    to keep referenced for the process lifetime, or None if another instance
    already holds the mutex."""
    handle = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        # Creation failing outright (access denied on the name, ...) is not
        # evidence of another instance; fail open rather than refuse to start.
        logger.warning(f"CreateMutexW({name!r}) failed with error {err}; "
                       "skipping the single-instance check.")
        return InstanceLock(None)
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return InstanceLock(handle)


def notify_already_running() -> None:
    """Give the user visible feedback for a second launch: re-show the running
    instance's console window (it may be hidden to the tray), else a message
    box.  The window is matched by PuiKit's window class *and* the console
    title - the class name is a PuiKit internal pinned here deliberately; if
    it ever changes, the fallback message box still gives feedback."""
    hwnd = user32.FindWindowW("PuiKitWindowClass", "Keyhac")
    if hwnd:
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)
    else:
        user32.MessageBoxW(None,
                           "Keyhac is already running (check the system tray).",
                           "Keyhac", MB_ICONINFORMATION)
