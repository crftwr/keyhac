"""Mechanized interactive pass of the Keyhac.exe bundle (Windows).

Covers the part of the "full interactive pass" (issue #10) that does not need
a human: the tool-window styles, the frame-autosave round trip through the
registry, rejection of a poisoned off-screen frame, and a clean quit. Those
are exactly what the tool-window / frame-autosave fixes changed, and what has
not been re-verified since.

Run it with no Keyhac running:

    python tools/bundle_pass.py

It refuses to start otherwise, because the single-instance guard would make
every check fail for the wrong reason. Build the bundle first
(`make windows-app`). Only stdlib and ctypes, so it needs no PYTHONPATH.

Quit is PostThreadMessage(WM_QUIT) to the app's UI thread - the same teardown
the tray's "Quit Keyhac" reaches: run_event_loop breaks, then main.py's
finally does hook.uninstall() + console.close(), and close() is what writes
the autosave frame.

What still needs a human afterwards: that the tray icon and its menu items
render and respond to a real click, and that the console's log pane, hook
checkbox and log-level dropdown behave when driven by hand.
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

if sys.platform != "win32":
    print("Windows only.")
    raise SystemExit(2)

import winreg

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = os.path.join(REPO_ROOT, "windows_app", "build", "Keyhac",
                           "Keyhac.exe")
ERROR_LOG = os.path.expanduser(r"~\.keyhac\keyhac-error.log")
AUTOSAVE_KEY = r"Software\PuiKit\FrameAutosave\KeyhacConsole"
MUTEX_NAME = "crftwr.Keyhac2.SingleInstance"

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GWL_EXSTYLE = -20
WM_QUIT = 0x0012
SYNCHRONIZE = 0x00100000

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
user32.MonitorFromRect.restype = wintypes.HMONITOR
kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMWINDOWSPROC, wintypes.LPARAM]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def another_instance_running() -> bool:
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def read_autosave():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSAVE_KEY) as key:
            return winreg.QueryValueEx(key, "Frame")[0]
    except FileNotFoundError:
        return None


def write_autosave(value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSAVE_KEY) as key:
        winreg.SetValueEx(key, "Frame", 0, winreg.REG_SZ, value)


def console_window(pid):
    """The PuiKit console window owned by `pid`, if it exists yet."""
    found = []

    def cb(hwnd, _lparam):
        wnd_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wnd_pid))
        if wnd_pid.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "PuiKitWindowClass":
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append((hwnd, title.value))
        return True

    user32.EnumWindows(ENUMWINDOWSPROC(cb), 0)
    return found[0] if found else (None, None)


def wait_for_console(proc, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return None, f"process exited early with code {proc.returncode}"
        hwnd, title = console_window(proc.pid)
        if hwnd:
            return hwnd, title
        time.sleep(0.3)
    return None, "timed out waiting for the console window"


def rect_of(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def on_a_monitor(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return user32.MonitorFromRect(ctypes.byref(r), 0) != 0


def quit_cleanly(proc, hwnd, timeout=30.0):
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", default=DEFAULT_EXE,
                        help="path to the built Keyhac.exe")
    args = parser.parse_args()

    if not os.path.exists(args.exe):
        print(f"Bundle not built: {args.exe}\nRun `make windows-app` first.")
        return 2
    if another_instance_running():
        print("Keyhac is already running in this session, so the "
              "single-instance guard would refuse every launch below.\n"
              "Quit it (tray -> Quit Keyhac) and re-run this script.")
        return 2

    original = read_autosave()
    print(f"exe: {args.exe}")
    print(f"saved the existing autosave frame: {original!r}\n")
    log_before = os.path.getmtime(ERROR_LOG) if os.path.exists(ERROR_LOG) else None
    procs = []

    def launch():
        proc = subprocess.Popen([args.exe], cwd=os.path.dirname(args.exe))
        procs.append(proc)
        return proc

    try:
        # --- run 1: an on-screen saved frame must be restored ------------
        planted = "220,180,900,620"
        write_autosave(planted)
        proc = launch()
        hwnd, title = wait_for_console(proc)
        check("bundle launches and shows its console", hwnd is not None,
              f"title={title!r}" if hwnd else str(title))
        if hwnd is None:
            return 1

        time.sleep(1.5)
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        check("console is a tool window (no taskbar button, no Alt-Tab)",
              bool(ex & WS_EX_TOOLWINDOW), f"exstyle={ex:#010x}")
        check("console is not forced into the taskbar",
              not (ex & WS_EX_APPWINDOW),
              f"WS_EX_APPWINDOW {'SET' if ex & WS_EX_APPWINDOW else 'clear'}")

        got, want = rect_of(hwnd), tuple(int(v) for v in planted.split(","))
        check("the saved frame is restored on launch", got == want,
              f"planted={want} actual={got}")

        # --- move, quit cleanly, the new frame must persist --------------
        moved = (300, 240, 800, 560)
        user32.MoveWindow(hwnd, *moved, True)
        time.sleep(1.0)
        check("quits cleanly on WM_QUIT (the tray Quit teardown)",
              quit_cleanly(proc, hwnd), f"exit code {proc.returncode}")
        check("the moved frame was written back on quit",
              read_autosave() == ",".join(str(v) for v in moved),
              f"registry={read_autosave()!r}")

        # --- run 2: a poisoned off-screen frame must be rejected ---------
        write_autosave("-32000,-32000,842,1139")
        proc2 = launch()
        hwnd2, title2 = wait_for_console(proc2)
        check("bundle launches with a poisoned off-screen frame",
              hwnd2 is not None, "" if hwnd2 else str(title2))
        if hwnd2 is not None:
            time.sleep(1.5)
            x, y, w, h = rect_of(hwnd2)
            check("the off-screen frame is rejected and the window lands on a "
                  "monitor",
                  x > -30000 and y > -30000 and on_a_monitor(hwnd2),
                  f"frame=({x},{y},{w},{h})")
            check("second run also quits cleanly",
                  quit_cleanly(proc2, hwnd2), f"exit code {proc2.returncode}")

        # --- run 3: the single-instance guard, cross process -------------
        proc3 = launch()
        hwnd3, _ = wait_for_console(proc3)
        if hwnd3 is not None:
            blocked = subprocess.run([args.exe], cwd=os.path.dirname(args.exe),
                                     capture_output=True, text=True, timeout=60)
            check("a second launch is refused by the single-instance guard",
                  "already running" in (blocked.stderr or "").lower(),
                  f"exit={blocked.returncode} stderr={blocked.stderr.strip()[:80]!r}")
            quit_cleanly(proc3, hwnd3)

        # --- the launcher's crash path must not have fired ---------------
        log_after = os.path.getmtime(ERROR_LOG) if os.path.exists(ERROR_LOG) else None
        check("the launcher wrote no keyhac-error.log",
              log_after == log_before,
              "unchanged" if log_after == log_before else f"WRITTEN: {ERROR_LOG}")

    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        if original is not None:
            write_autosave(original)
            print(f"\nrestored the original autosave frame: {original!r}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n==== {passed}/{len(results)} bundle checks passed ====")
    if passed == len(results):
        print("Still needs a human: tray icon + menu clicks, and the console's "
              "log pane / hook checkbox / log-level dropdown by hand.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
