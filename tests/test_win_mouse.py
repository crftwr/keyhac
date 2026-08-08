"""Windows mouse injection + WH_MOUSE_LL - live.

Briefly moves the real cursor (and restores it) and clicks/wheels into a
probe window this process owns. Skips rather than fails if the
environment refuses keyboard focus.
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
user32.PeekMessageW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEWHEEL = 0x0201, 0x0202, 0x020A


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]


class MouseProbe:
    def __init__(self):
        self.events = []  # (msg, wheel_delta)

        def _proc(hwnd, msg, wparam, lparam):
            if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEWHEEL):
                delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
                self.events.append((msg, delta))
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc_ref = WNDPROC(_proc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc_ref
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "KeyhacMouseProbe"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, "KeyhacMouseProbe", "Keyhac mouse probe", 0x00CF0000,
            120, 120, 260, 160, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW: {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, 5)

    def pump(self, seconds):
        end = time.monotonic() + seconds
        msg = ctypes.create_string_buffer(48)
        while time.monotonic() < end:
            while user32.PeekMessageW(msg, None, 0, 0, 1):
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.005)

    def destroy(self):
        user32.DestroyWindow(self.hwnd)


@pytest.fixture(scope="module")
def hook():
    return WinInputHook()


@pytest.fixture(scope="module")
def _probe_window():
    # Module-scoped deliberately: the window class is registered once with
    # this instance's wndproc thunk; a per-test probe would re-use the class
    # whose lpfnWndProc points at the previous (freed) thunk and crash.
    probe = MouseProbe()
    yield probe
    probe.destroy()


@pytest.fixture()
def probe(_probe_window):
    _probe_window.events.clear()
    return _probe_window


def pointer_is_quiet(hook, samples: int = 3, gap: float = 0.03) -> bool:
    """Whether anything *else* is driving the pointer right now.

    These tests inject a move and then assert where the cursor ended up, which
    is only meaningful when nobody else is moving it. A window opening and
    taking foreground, or a session where a human or another harness is using
    the mouse, perturbs it - and the observed failures all happened during a
    run where other windows were being opened and closed throughout.

    The distinction this buys is worth the three samples: a wrong answer while
    the pointer is quiet is a defect, and the same answer while something else
    is moving it is noise. Without the check both look identical, and the
    module's own docstring already promises to skip on a hostile environment
    rather than fail.
    """
    first = hook.cursor_pos()
    for _ in range(samples):
        time.sleep(gap)
        if hook.cursor_pos() != first:
            return False
    return True


@pytest.fixture()
def restore_cursor(hook):
    saved = hook.cursor_pos()
    yield saved
    hook.send_mouse([("move", 0, 0)])  # flush any pending coalescing
    # jump straight back to where the user's cursor was
    cur = hook.cursor_pos()
    hook.send_mouse([("move", saved[0] - cur[0], saved[1] - cur[1])])


def test_cursor_pos_and_relative_move(hook, restore_cursor):
    # Retried: a human moving the physical mouse mid-test perturbs the
    # cursor; any single clean attempt proves the injection.
    landed = None
    attempted = 0
    for _attempt in range(3):
        if not pointer_is_quiet(hook):
            continue                    # something else owns the pointer
        attempted += 1
        x0, y0 = hook.cursor_pos()
        hook.send_mouse([("move", 17, 11)])
        time.sleep(0.05)
        x1, y1 = hook.cursor_pos()
        if abs(x1 - (x0 + 17)) <= 1 and abs(y1 - (y0 + 11)) <= 1:
            return
        landed = ((x1, y1), (x0 + 17, y0 + 11))
    if not attempted:
        pytest.skip("something else is moving the pointer")
    pytest.fail(f"cursor went to {landed[0]}, expected {landed[1]}")


def test_click_lands_in_probe_window(hook, probe, restore_cursor):
    WinWindow(probe.hwnd).activate()
    probe.pump(0.1)
    x, y, w, h = WinWindow(probe.hwnd).get_frame()
    cx, cy = int(x + w / 2), int(y + h / 2)
    cur = hook.cursor_pos()
    hook.send_mouse([("move", cx - cur[0], cy - cur[1]),
                     ("left", True), ("left", False)])
    probe.pump(0.3)
    msgs = [m for m, _d in probe.events]
    assert WM_LBUTTONDOWN in msgs and WM_LBUTTONUP in msgs


def test_wheel_reaches_focused_probe(hook, probe, restore_cursor):
    WinWindow(probe.hwnd).activate()
    user32.SetFocus(probe.hwnd)
    probe.pump(0.1)
    if user32.GetFocus() != probe.hwnd:
        pytest.skip("environment refused keyboard focus")
    # Hover the probe too: Windows may route the wheel by hover, not focus
    x, y, w, h = WinWindow(probe.hwnd).get_frame()
    cur = hook.cursor_pos()
    hook.send_mouse([("move", int(x + w / 2) - cur[0], int(y + h / 2) - cur[1]),
                     ("wheel", 1.0)])
    probe.pump(0.3)
    wheels = [d for m, d in probe.events if m == WM_MOUSEWHEEL]
    assert wheels == [120]


def test_mouse_hook_classifies_own_vs_real(hook, probe):
    """WH_MOUSE_LL: an untagged wheel counts as physical (one-shot cancel
    fires); our own tagged output is ignored."""
    seen = []
    hook.install(lambda e: False, lambda: None, lambda: seen.append(1))
    try:
        # Untagged = physical. Raw SendInput, not hook.send_mouse.
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT), ("pad", ctypes.c_ubyte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT),
                                     ctypes.c_int]
        inp = INPUT()
        inp.type = 0
        inp.mi = MOUSEINPUT(0, 0, 120, 0x0800, 0, 0)  # wheel, no sentinel
        # A control pump before injecting anything. A physical wheel or a
        # window opening under the pointer also arrives as an untagged event,
        # and would be indistinguishable from the one this test is about to
        # send - so establish that the machine is quiet first, and skip rather
        # than measure noise.
        probe.pump(0.3)
        if seen:
            pytest.skip("the physical mouse is in use")

        # Retried, because the event can be dropped before any hook sees it.
        # Measured: SendInput returns 1 with no error, the WH_MOUSE_LL handle
        # is still valid, and no callback arrives - occasionally, and more
        # often when other injection preceded it. A second wheel is then seen
        # normally, so the hook is alive and the *event* went missing.
        #
        # This cannot hide the thing the test is for. If an untagged wheel were
        # misclassified as our own output, no attempt would ever be counted and
        # the assertion below still fails; the retry only removes the
        # dependency on one particular injected event surviving.
        for _attempt in range(5):
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            probe.pump(0.3)
            if seen:
                break
        assert seen == [1]

        # Same again before the second half: any event arriving during a quiet
        # window here is the environment's, and one arriving after the tagged
        # send would otherwise be blamed on the classifier.
        probe.pump(0.3)
        if seen != [1]:
            pytest.skip("a physical mouse event arrived mid-test")

        hook.send_mouse([("wheel", -1.0)])  # own output: must NOT count
        probe.pump(0.3)
        assert seen == [1]
    finally:
        hook.uninstall()
