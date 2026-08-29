"""send_text (KEYEVENTF_UNICODE) - live: inject into our own probe window.

The probe window collects WM_CHAR, so the test exercises the full path a
real app sees: SendInput VK_PACKET units -> TranslateMessage -> WM_CHAR.
Briefly takes keyboard focus; skips (not fails) if the environment refuses
to grant it.
"""

import ctypes
import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.platform.win.hook import WinInputHook  # noqa: E402
from keyhac.platform.win.window import WinWindow  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.argtypes = []
user32.GetFocus.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.PeekMessageW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WM_CHAR = 0x0102


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]


class CharProbe:
    """A window that records every WM_CHAR UTF-16 unit it receives."""

    def __init__(self):
        self.units: list[int] = []

        def _proc(hwnd, msg, wparam, lparam):
            if msg == WM_CHAR:
                self.units.append(wparam & 0xFFFF)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(_proc)  # must outlive the window
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "KeyhacSendTextProbe"
        user32.RegisterClassW(ctypes.byref(wc))  # repeat registration: benign
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        self.hwnd = user32.CreateWindowExW(
            0, "KeyhacSendTextProbe", "send_text probe", WS_OVERLAPPEDWINDOW,
            60, 60, 300, 100, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW: {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, 5)  # SW_SHOW

    def pump(self, seconds: float) -> None:
        # TranslateMessage is load-bearing: VK_PACKET becomes WM_CHAR only
        # through it - this is the path a real app's message loop takes.
        end = time.monotonic() + seconds
        msg = ctypes.create_string_buffer(48)
        while time.monotonic() < end:
            while user32.PeekMessageW(msg, None, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.005)

    def text(self) -> str:
        return bytes(
            b for unit in self.units for b in unit.to_bytes(2, "little")
        ).decode("utf-16-le", errors="surrogatepass")

    def destroy(self) -> None:
        user32.DestroyWindow(self.hwnd)


@pytest.fixture(scope="module")
def probe():
    probe = CharProbe()
    WinWindow(probe.hwnd).activate()
    user32.SetFocus(probe.hwnd)
    probe.pump(0.2)
    if user32.GetFocus() != probe.hwnd:
        probe.destroy()
        pytest.skip("environment refused keyboard focus")
    yield probe
    probe.destroy()


@pytest.mark.parametrize("text", [
    "Hello, world!",
    "日本語入力のテスト",
    "emoji \U0001f3b9 and \U00020bb7野家",
])
def test_send_text_arrives_as_wm_char(probe, text):
    probe.units.clear()
    WinInputHook().send_text(text)
    deadline = time.monotonic() + 3.0
    expected_units = len(text.encode("utf-16-le")) // 2
    while len(probe.units) < expected_units and time.monotonic() < deadline:
        probe.pump(0.05)
    assert probe.text() == text


def test_send_text_types_a_lone_surrogate_rather_than_raising(probe):
    """Text read back from a UTF-16 buffer that was cut between the halves of
    a pair carries a lone surrogate. Strict UTF-16 refuses to encode it, so
    typing such a string raised from the middle of the action instead of
    typing the one broken character - and the probe below reads its units back
    with surrogatepass precisely because a unit is what was sent."""
    probe.units.clear()
    broken = "cut\ud842"
    WinInputHook().send_text(broken)
    deadline = time.monotonic() + 3.0
    while len(probe.units) < 4 and time.monotonic() < deadline:
        probe.pump(0.05)
    assert probe.text() == broken
