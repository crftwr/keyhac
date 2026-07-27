"""Windows focus provider + UI Automation element access.

These pin the hand-written COM vtable slot indices in
keyhac/platform/win/uielement.py: a wrong index silently calls a *different*
method, so every accessor is cross-checked against the Win32 answer for the
same window rather than merely asserted non-None. (That is how the
BoundingRectangle slot was caught returning NaNs from a doubles-vs-LONGs
struct mismatch.)
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.platform.win.focus import WinFocusProvider, _component  # noqa: E402
from keyhac.platform.win.uielement import UIElement, get_automation  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetDesktopWindow.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND


def _win32_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


@pytest.fixture(scope="module")
def automation():
    if get_automation() is None:
        pytest.skip("UI Automation unavailable on this machine")
    return True


class TestUIElementAgainstWin32:

    def test_class_name_and_process_match_win32(self, automation):
        hwnd = user32.GetDesktopWindow()
        element = UIElement.from_hwnd(hwnd)
        assert element is not None
        assert element.get_attribute_value("ClassName") == _win32_class_name(hwnd)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        assert element.get_attribute_value("ProcessId") == pid.value

    def test_native_window_handle_round_trips(self, automation):
        hwnd = user32.GetDesktopWindow()
        element = UIElement.from_hwnd(hwnd)
        assert element.get_attribute_value("NativeWindowHandle") == int(hwnd)

    def test_bounding_rectangle_matches_getwindowrect(self, automation):
        # RECT of LONGs, not UiaRect of doubles - reading the wrong struct
        # produced NaNs here.
        hwnd = user32.GetDesktopWindow()
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        expected = (rect.left, rect.top,
                    rect.right - rect.left, rect.bottom - rect.top)
        assert UIElement.from_hwnd(hwnd).get_attribute_value("BoundingRectangle") == expected

    def test_control_type_is_a_known_name(self, automation):
        element = UIElement.from_hwnd(user32.GetDesktopWindow())
        assert element.get_attribute_value("ControlType") == "Pane"
        assert element.get_attribute_value("ControlTypeId") == 50033

    def test_attribute_names_are_all_readable(self, automation):
        element = UIElement.from_hwnd(user32.GetDesktopWindow())
        for name in element.get_attribute_names():
            element.get_attribute_value(name)  # must not raise

    def test_unknown_attribute_returns_none(self, automation):
        element = UIElement.from_hwnd(user32.GetDesktopWindow())
        assert element.get_attribute_value("AXRole") is None  # macOS vocabulary

    def test_from_hwnd_rejects_null(self, automation):
        assert UIElement.from_hwnd(0) is None


class TestFocusPath:

    def test_path_is_a_full_hierarchy_rooted_at_the_application(self):
        focus = WinFocusProvider().get_focus()
        if focus is None:
            pytest.skip("no foreground window")
        assert focus.path.startswith("/Application(")
        # Window level at minimum; a real UIA walk adds the controls below it.
        assert focus.path.count("/") >= 2
        assert focus.app_name and focus.class_name is not None

    def test_path_components_are_transliterated(self):
        # fnmatch metacharacters and separators in a title must not be able to
        # break a pattern, so they are transliterated (same table as macOS).
        assert _component("Window", "a/b(c)[d]*?") == "Window(a-b<c><d>--)"

    def test_focus_is_cached_between_identical_probes(self):
        provider = WinFocusProvider()
        first = provider.get_focus()
        if first is None:
            pytest.skip("no foreground window")
        # Same window, same title -> the expensive tier must not run again.
        assert provider.get_focus() is first

    def test_cache_invalidates_when_the_probe_changes(self):
        provider = WinFocusProvider()
        first = provider.get_focus()
        if first is None:
            pytest.skip("no foreground window")
        provider._probe = (0, 0, "something else")
        assert provider.get_focus() is not first
