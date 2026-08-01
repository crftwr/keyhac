"""BalloonManager live on Windows - frameless no-activate secondary HWND.

Mirrors the macOS live verification: the balloon window exists, is visible
without stealing foreground, sits in the main screen's work area, and
closes cleanly.
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.ui.balloon import BalloonManager  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


@pytest.fixture(scope="module")
def backend():
    puikit = pytest.importorskip("puikit.backends")
    backend = puikit.create_backend(
        "gui", width=40, height=10, title="keyhac-balloon-test")
    backend.open()
    yield backend
    backend.close()


def test_balloon_pops_and_closes(backend):
    manager = BalloonManager(backend)
    fg_before = user32.GetForegroundWindow()

    manager.pop("test", "multi-stroke: LEADER-")
    handle, _cancel = manager._balloons["test"]
    hwnd = handle.hwnd
    assert user32.IsWindowVisible(hwnd)

    # No-activate: popping the balloon must not move foreground.
    assert user32.GetForegroundWindow() == fg_before

    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    assert ex & WS_EX_TOPMOST
    assert ex & WS_EX_NOACTIVATE
    assert ex & WS_EX_TOOLWINDOW

    # Placement: inside the main screen's work area (top-right corner).
    x, y, w, h = handle.frame_px()
    (_full, (vx, vy, vw, vh)) = backend.screen_frames()[0]
    assert vx <= x and x + w <= vx + vw
    assert vy <= y and y + h <= vy + vh

    manager.close("test")
    assert not manager._balloons
    assert not user32.IsWindowVisible(hwnd)


def test_balloon_replaces_same_name(backend):
    manager = BalloonManager(backend)
    manager.pop("name", "first")
    first_hwnd = manager._balloons["name"][0].hwnd
    manager.pop("name", "second, a fair bit longer than the first")
    second_hwnd = manager._balloons["name"][0].hwnd
    assert first_hwnd != second_hwnd
    assert not user32.IsWindowVisible(first_hwnd)
    assert user32.IsWindowVisible(second_hwnd)
    manager.close()
    assert not manager._balloons
