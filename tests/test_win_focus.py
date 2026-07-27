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


class TestUIAPatterns:
    """Actions and pattern-backed attributes.

    Pattern vtable slots need the same live cross-check as the element ones:
    IUIAutomationTextRange::GetText sits at 12, and calling slot 11
    (GetEnclosingElement, whose out-param is an element pointer) as
    GetText(int, BSTR*) access-violates rather than failing quietly.
    """

    def test_unknown_action_is_refused_not_raised(self, automation):
        element = UIElement.from_hwnd(user32.GetDesktopWindow())
        # A macOS action name reaching a Windows element must not raise on the
        # key path; it logs the available names and returns False.
        assert element.perform_action("AXPress") is False

    def test_action_names_are_only_the_supported_ones(self, automation):
        element = UIElement.from_hwnd(user32.GetDesktopWindow())
        names = element.get_action_names()
        assert set(names) <= {"Invoke", "Toggle", "Expand", "Collapse"}

    def test_pattern_attributes_are_listed_only_when_supported(self, automation):
        # The desktop pane is not a value/text control, so those attributes are
        # absent from its names - get_attribute_names answers "what can I read
        # from *this* element", like AXUIElementCopyAttributeNames.
        names = UIElement.from_hwnd(user32.GetDesktopWindow()).get_attribute_names()
        assert "Value" not in names and "SelectedText" not in names
        assert "Name" in names and "ControlType" in names

    def test_unsupported_pattern_attribute_reads_none(self, automation):
        element = UIElement.from_hwnd(user32.GetDesktopWindow())
        assert element.get_attribute_value("Value") is None
        assert element.get_attribute_value("SelectedText") is None


@pytest.mark.slow
class TestUIAPatternsAgainstNotepad:
    """The read/write paths against a real editable control.

    Launches Notepad, so it is marked slow and skips cleanly if unavailable.
    """

    @pytest.fixture
    def edit(self, automation):
        import subprocess
        import time
        from keyhac.platform.win.window import WinWindowProvider
        from keyhac.platform.win.uielement import (
            _control_view_walker, _element_out, _IUIAutomationTreeWalker)

        try:
            process = subprocess.Popen(["notepad.exe"])
        except OSError:
            pytest.skip("notepad.exe unavailable")
        try:
            window = None
            for _ in range(30):
                time.sleep(0.1)
                window = WinWindowProvider().find_window(app="notepad")
                if window is not None:
                    break
            if window is None:
                pytest.skip("Notepad window did not appear")

            def walk(element, depth=0):
                if element.get_attribute_value("ControlType") in ("Edit", "Document"):
                    return element
                if depth > 5:
                    return None
                walker = _control_view_walker()
                child = _element_out(walker, _IUIAutomationTreeWalker.GetFirstChildElement,
                                     ctypes.c_void_p(element._ptr.value))
                while child:
                    node = UIElement(child)
                    found = walk(node, depth + 1)
                    if found is not None:
                        return found
                    child = _element_out(walker, _IUIAutomationTreeWalker.GetNextSiblingElement,
                                         ctypes.c_void_p(node._ptr.value))
                return None

            found = walk(UIElement.from_hwnd(window.hwnd))
            if found is None:
                pytest.skip("no editable element found in Notepad")
            yield found
        finally:
            process.terminate()

    def test_value_round_trips(self, edit):
        assert "Value" in edit.get_attribute_names()
        assert edit.set_value("Hello UIA world")
        assert edit.get_attribute_value("Value") == "Hello UIA world"
        assert edit.get_attribute_value("IsReadOnly") is False

    def test_selected_text_reads_the_selection(self, edit):
        # The Windows answer to keyhac-mac's "AXSelectedText".
        import time
        edit.set_value("selection probe")
        edit.set_focus()
        time.sleep(0.2)
        for vk, up in ((0x11, 0), (0x41, 0), (0x41, 2), (0x11, 2)):  # Ctrl+A
            user32.keybd_event(vk, 0, up, 0)
        time.sleep(0.3)
        assert edit.get_attribute_value("SelectedText") == "selection probe"
