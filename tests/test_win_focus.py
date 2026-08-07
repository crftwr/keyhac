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


TAB_LABELS = ("Alpha", "Beta", "Gamma")
TCM_GETCURSEL = 0x130B
TCM_INSERTITEMW = 0x133E
TCIF_TEXT = 0x0001


def _selected_tab(hwnd) -> int:
    """Which tab the control itself says is current. Not reachable via UIA."""
    return user32.SendMessageW(hwnd, TCM_GETCURSEL, None, None)


@pytest.fixture(scope="module")
def tab_control(automation):
    """A real SysTabControl32 with three tabs, shown but never activated."""
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Mandatory, and the trap this file's neighbours already document: without
    # an explicit restype ctypes returns c_int and truncates the HINSTANCE to
    # 32 bits. The handle then looks plausible and CreateWindowExW faults on
    # it - which is exactly what happened while writing this fixture.
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p

    class INITCOMMONCONTROLSEX(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("dwICC", wintypes.DWORD)]

    class TCITEMW(ctypes.Structure):
        _fields_ = [("mask", wintypes.UINT), ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("pszText", wintypes.LPWSTR),
                    ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
                    ("lParam", wintypes.LPARAM)]

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]

    icc = INITCOMMONCONTROLSEX(ctypes.sizeof(INITCOMMONCONTROLSEX), 0x08)
    comctl32.InitCommonControlsEx(ctypes.byref(icc))
    instance = kernel32.GetModuleHandleW(None)

    WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE = 0x00CF0000, 0x40000000, 0x10000000
    parent = user32.CreateWindowExW(
        0, "STATIC", "keyhac SelectionItem pin", WS_OVERLAPPEDWINDOW,
        60, 60, 420, 260, None, None, instance, None)
    if not parent:
        pytest.skip("could not create the host window")
    tab = user32.CreateWindowExW(
        0, "SysTabControl32", None, WS_CHILD | WS_VISIBLE,
        10, 10, 380, 200, parent, None, instance, None)
    if not tab:
        user32.DestroyWindow(parent)
        pytest.skip("SysTabControl32 unavailable")

    for index, label in enumerate(TAB_LABELS):
        item = TCITEMW(mask=TCIF_TEXT, pszText=label)
        user32.SendMessageW(tab, TCM_INSERTITEMW, index, ctypes.byref(item))
    # Shown, never activated: this must not take focus from whatever else the
    # suite is driving.
    user32.ShowWindow(parent, 4)  # SW_SHOWNOACTIVATE

    element = UIElement.from_hwnd(tab)
    if element is None or not element.children():
        user32.DestroyWindow(parent)
        pytest.skip("UI Automation did not expose the tab items")
    yield element, tab
    user32.DestroyWindow(parent)


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
        assert set(names) <= {"Invoke", "Toggle", "Expand", "Collapse", "Select"}

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


class TestSelectionItemAgainstATabControl:
    """SelectionItem, pinned against the tab control's own Win32 answer.

    This pattern exists because nothing else reaches a tab. A Win32 TabItem
    supports no Invoke, no Toggle and no Expand - `get_action_names()` on one
    returns `[]` without it - and its selected-ness is not a value, so `.value`
    reads None however the tab is set. Both facts were measured against a real
    property sheet (Internet Properties) while porting an action that had only
    ever run on macOS, where a tab is an AXRadioButton with AXValue "1" and
    answers AXPress.

    `TCM_GETCURSEL` is the control's own truth and is not reachable through
    UIA, so it cannot agree with a wrong vtable index by accident - which is
    what this file exists to rule out.
    """

    def test_the_items_are_tab_items(self, tab_control):
        element, _tab = tab_control
        items = element.children()
        assert [i.get_attribute_value("Name") for i in items] == list(TAB_LABELS)
        assert {i.get_attribute_value("ControlType") for i in items} == {"TabItem"}

    def test_select_is_the_only_action_a_tab_item_offers(self, tab_control):
        element, _tab = tab_control
        assert element.children()[0].get_action_names() == ["Select"]

    def test_is_selected_matches_tcm_getcursel(self, tab_control):
        element, tab = tab_control
        current = _selected_tab(tab)
        states = [i.get_attribute_value("IsSelected") for i in element.children()]
        assert states == [index == current for index in range(len(TAB_LABELS))]

    def test_select_moves_the_selection_and_win32_agrees(self, tab_control):
        element, tab = tab_control
        target = 2 if _selected_tab(tab) != 2 else 0
        assert element.children()[target].perform_action("Select") is True
        assert _selected_tab(tab) == target
        assert element.children()[target].get_attribute_value("IsSelected") is True

    def test_a_tab_item_has_no_value_to_read(self, tab_control):
        """Why the macOS approach does not port: there is nothing in `.value`.

        `snapshot_settings.py` finds the current tab with `str(tab.value) == "1"`,
        which on Windows is None for every tab, selected or not.
        """
        element, tab = tab_control
        element.children()[1].perform_action("Select")
        assert _selected_tab(tab) == 1
        assert all(i.get_attribute_value("Value") is None
                   for i in element.children())


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
            yield window, found
        finally:
            process.terminate()

    def test_value_round_trips(self, edit):
        _window, element = edit
        assert "Value" in element.get_attribute_names()
        assert element.set_value("Hello UIA world")
        assert element.get_attribute_value("Value") == "Hello UIA world"
        assert element.get_attribute_value("IsReadOnly") is False

    def test_selected_text_reads_the_selection(self, edit):
        """The Windows answer to keyhac-mac's "AXSelectedText".

        Selecting needs real keyboard focus, which no test can guarantee on a
        shared desktop - another app may take the foreground at any moment. So
        this activates the window, polls for the selection, and *skips* rather
        than fails if focus never landed; a wrong answer still fails.
        """
        import time
        window, element = edit
        element.set_value("selection probe")
        window.activate()
        time.sleep(0.3)
        for vk, flags in ((0x11, 0), (0x41, 0), (0x41, 2), (0x11, 2)):  # Ctrl+A
            user32.keybd_event(vk, 0, flags, 0)
        selected = ""
        for _ in range(20):
            time.sleep(0.1)
            selected = element.get_attribute_value("SelectedText") or ""
            if selected:
                break
        if not selected:
            pytest.skip("could not take keyboard focus (another app has it)")
        assert selected == "selection probe"
