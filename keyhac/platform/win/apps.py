"""Windows application control.

STATUS: activate_pid verified live on Windows (chooser-focus session: a
two-process probe armed the foreground lock with synthesized input and
confirmed plain SetForegroundWindow is refused while this path wins
foreground + keyboard focus). launch() verified live on Windows
(tests/test_win_start_menu.py). edit_file() written to spec, not yet run.
"""

import ctypes
import ntpath
import os
import sys

from keyhac.core import log
from keyhac.platform.base import AppControl
from keyhac.platform.win.startmenu import (
    find_start_menu_app, forget_start_menu_apps, scan_start_menu)

logger = log.getLogger("WinApps")

if sys.platform == "win32":
    from ctypes import wintypes

    # Foregrounding delegates to WinWindow.activate(), which carries the
    # AttachThreadInput foreground-lock workaround (a background process --
    # Keyhac at chooser-open time -- is refused a plain SetForegroundWindow).
    from keyhac.platform.win.window import WinWindow

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [ENUMWINDOWSPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    SW_SHOWNORMAL = 1

    shlwapi = ctypes.WinDLL("shlwapi", use_last_error=True)
    shlwapi.AssocQueryStringW.argtypes = [
        wintypes.DWORD, ctypes.c_int, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    # Not ctypes.HRESULT, which raises where this is an answer to read.
    shlwapi.AssocQueryStringW.restype = ctypes.c_long

    ASSOCF_IS_PROTOCOL = 0x1000
    ASSOCSTR_EXECUTABLE = 2


#: Each Chromium browser: its name, its data folder under %LOCALAPPDATA%, and the
#: executable, which App Paths resolves.
_CHROMIUM_BROWSERS = (
    ("Chrome", r"Google\Chrome\User Data", "chrome.exe"),
    ("Edge", r"Microsoft\Edge\User Data", "msedge.exe"),
)


def _default_browser_executable() -> str | None:
    """The file name of what opens an https link, or None if Windows does not say.

    By executable and not by ProgId: a per-user Chrome's ProgId carries a suffix
    unique to the installation.
    """
    if sys.platform != "win32":
        return None
    size = wintypes.DWORD(1024)
    path = ctypes.create_unicode_buffer(size.value)
    result = shlwapi.AssocQueryStringW(
        ASSOCF_IS_PROTOCOL, ASSOCSTR_EXECUTABLE, "https", "open", path, ctypes.byref(size))
    if result != 0 or not path.value:
        return None
    return ntpath.basename(path.value)


class WinAppControl(AppControl):

    def activate_pid(self, pid: int) -> bool:
        """Foreground the process's first visible top-level window in Z-order
        (for our own pid that is the topmost chooser, not the console).
        WinWindow.activate() restores a minimized window first."""
        if sys.platform != "win32":
            return False
        found = []

        def _cb(hwnd, lparam):
            wnd_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wnd_pid))
            if wnd_pid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(ENUMWINDOWSPROC(_cb), 0)
        if not found:
            return False
        return WinWindow(found[0]).activate()

    def launch(self, app_name: str) -> None:
        """A Start menu entry of that name if there is one, whatever PATH
        holds, otherwise whatever the shell makes of the name."""
        target = find_start_menu_app(app_name)
        if target is not None:
            try:
                os.startfile(f"shell:AppsFolder\\{target}")
                return
            except OSError as e:
                logger.warning(f"Start menu entry {app_name!r} would not "
                               f"launch: {e}")
                forget_start_menu_apps()
        os.startfile(app_name)

    def installed_apps(self) -> list[tuple[str, str]]:
        """What the Start menu lists.  launch() takes the display name, and
        resolves a name used twice to the Store application."""
        try:
            entries = scan_start_menu()
        except OSError as e:
            logger.warning(f"Could not read the Start menu's applications: {e}")
            return []
        names = {name.casefold(): name for name, _parsing in entries}
        return [(name, name) for name in names.values()]

    def browser_data_dirs(self) -> list[tuple[str, str]]:
        """The default browser's alone, so none when that is another browser.  If
        Windows does not say which is the default, both."""
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return []
        default = _default_browser_executable()
        return [(name, os.path.join(local, folder))
                for name, folder, exe in _CHROMIUM_BROWSERS
                if default is None or exe.casefold() == default.casefold()]

    def open_url_in_browser(self, browser: str, url: str, profile: str) -> None:
        if sys.platform != "win32":
            return
        exe = next(exe for name, _folder, exe in _CHROMIUM_BROWSERS if name == browser)
        # A quote would end the argument.  In a URL it means the same escaped.
        quoted = url.replace('"', "%22")
        result = shell32.ShellExecuteW(
            None, None, exe, f'--profile-directory="{profile}" "{quoted}"',
            None, SW_SHOWNORMAL)
        if (result or 0) <= 32:
            logger.warning(f"Could not open {url} in {browser} (ShellExecute {result}).")

    def open_url(self, url: str) -> None:
        # startfile on a URL is ShellExecute "open", which is what the shell
        # does for a link: the registered handler for the scheme, not a browser
        # we picked.
        if sys.platform != "win32":
            return
        try:
            os.startfile(url)
        except OSError as e:
            logger.warning(f"Could not open {url}: {e}")

    def edit_file(self, path: str, editor: str | None = None) -> None:
        """ShellExecute the editor with the quoted path (the keyhac-win
        editTextFile call: PATH and App Paths both resolve the name, so
        "notepad.exe" and registered editors like "Code.exe" work)."""
        if sys.platform != "win32":
            return
        editor = editor or "notepad.exe"
        result = shell32.ShellExecuteW(
            None, None, editor, f'"{path}"', None, SW_SHOWNORMAL)
        # Documented contract: the pseudo-HINSTANCE is > 32 on success.
        if (result or 0) <= 32:
            logger.warning(f"Could not open {path} with {editor} "
                           f"(ShellExecute code {result}).")
