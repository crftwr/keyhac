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
from keyhac.platform.win.uielement import (UIElement, _same_element,  # noqa: E402
                                           get_automation)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetDesktopWindow.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND


user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

WM_CLOSE = 0x0010


def _win32_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _notepad_hwnds():
    """Every top-level Notepad window on screen, by HWND."""
    from keyhac.platform.win.window import WinWindowProvider
    return {int(w.hwnd) for w in WinWindowProvider().list_windows()
            if w.class_name == "Notepad"}


def _close_window(hwnd, timeout=3.0):
    """Ask one window to close, and wait until it is gone."""
    import time
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not user32.IsWindow(hwnd):
            return True
        time.sleep(0.05)
    return False


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
    The window it opens is the only one it touches, and it closes it again.
    """

    @pytest.fixture
    def edit(self, automation):
        import subprocess
        import time
        from keyhac.platform.win.window import WinWindow
        from keyhac.platform.win.uielement import (
            _control_view_walker, _element_out, _IUIAutomationTreeWalker)

        # Which Notepad window is ours has to be answered by hand, twice
        # over. Windows 11 ships Notepad as a packaged app: System32's
        # notepad.exe is a stub that hands the work to one process shared by
        # every Notepad window, so the Popen handle is not the window's
        # process, terminating it closes nothing, and the windows this suite
        # opened used to pile up on the desktop. And "the Notepad window" is
        # not a thing either - a developer running the suite may well have
        # one open on a real file, which is not a window to type into or to
        # close. Both are answered by the window that was not there before.
        before = _notepad_hwnds()
        try:
            process = subprocess.Popen(["notepad.exe"])
        except OSError:
            pytest.skip("notepad.exe unavailable")
        window = None
        found = None
        try:
            for _ in range(30):
                time.sleep(0.1)
                fresh = _notepad_hwnds() - before
                if fresh:
                    window = WinWindow(fresh.pop())
                    break
            if window is None:
                # Notepad can be set to open in a tab of the window that is
                # already up; then there is nothing of ours here at all.
                pytest.skip("Notepad did not open a window of its own")

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
            # Leave the document empty so the close is not answered by a
            # save prompt, which would outlive the test as surely as the
            # window did.
            if found is not None:
                try:
                    found.set_value("")
                except Exception:
                    pass
            if window is not None:
                _close_window(window.hwnd)
            # The stub, on the chance this is the classic single-process
            # Notepad and the window did not take the process with it.
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


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p),
                ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p),
                ("hwndCaret", ctypes.c_void_p), ("rcCaret", wintypes.RECT)]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.c_void_p
GA_ROOT = 2


def _keyboard_focus():
    """The HWND holding the keyboard focus, system-wide, or 0.

    Thread 0 is the foreground thread, whose focus window is the one
    keystrokes actually reach - the Win32 answer set_focus() is graded against
    below. `GetFocus()` would answer only for this thread.
    """
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


#: A window with the four controls the focus tests need, run as its own
#: program.  In its own process because focus is not something a thread that
#: is not pumping messages can take: created inside the pytest process, these
#: controls report "could not take keyboard focus" forever and every test
#: below skips.  (Which the fixture found out by doing it.)
FOCUS_PROBE_SOURCE = r"""
import ctypes, sys
from ctypes import wintypes
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
wc = WNDCLASSW()
wc.lpfnWndProc = proc
wc.hInstance = kernel32.GetModuleHandleW(None)
wc.lpszClassName = "KeyhacSetFocusProbe"
user32.RegisterClassW(ctypes.byref(wc))
WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE = 0x00CF0000, 0x40000000, 0x10000000
WS_TABSTOP, WS_BORDER = 0x00010000, 0x00800000
CBS_DROPDOWN, CBS_HASSTRINGS = 0x0002, 0x0200
hwnd = user32.CreateWindowExW(0, "KeyhacSetFocusProbe", "keyhac set_focus pin",
                              WS_OVERLAPPEDWINDOW, 60, 60, 420, 300,
                              None, None, wc.hInstance, None)
def child(cls, style, y, height=26):
    return user32.CreateWindowExW(0, cls, "probe",
                                  WS_CHILD | WS_VISIBLE | style,
                                  10, y, 200, height, hwnd, None,
                                  wc.hInstance, None)
edit = child("EDIT", WS_TABSTOP | WS_BORDER, 10)
other = child("EDIT", WS_TABSTOP | WS_BORDER, 46)
label = child("STATIC", 0, 82)
combo = child("COMBOBOX", WS_TABSTOP | CBS_DROPDOWN | CBS_HASSTRINGS, 118, 200)
# Shown, never activated: what brings it forward is the set_focus() under
# test, which is what an action does to a window the user is not in.
user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
print(" ".join(str(int(h)) for h in (hwnd, edit, other, label, combo)), flush=True)
msg = ctypes.create_string_buffer(48)
while user32.GetMessageW(msg, None, 0, 0) > 0:
    user32.TranslateMessage(msg)
    user32.DispatchMessageW(msg)
"""


@pytest.fixture(scope="module")
def focus_probe(automation):
    """The probe window, and the HWNDs of its controls."""
    import subprocess
    import time

    process = subprocess.Popen([sys.executable, "-c", FOCUS_PROBE_SOURCE],
                               stdout=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline().split()
        if len(line) != 5:
            pytest.skip("the probe window did not come up")
        window, edit, other, label, combo = (int(h) for h in line)
        time.sleep(0.3)
        if UIElement.from_hwnd(edit) is None:
            pytest.skip("UI Automation did not expose the probe controls")
        yield {"window": window, "edit": edit, "other": other,
               "label": label, "combo": combo}
    finally:
        process.terminate()


def _focus_settles_on(hwnd, timeout=1.0):
    """Whether the keyboard focus arrives at `hwnd` within `timeout`.

    Polled rather than read once: GetGUIThreadInfo is an observer of a focus
    change the target thread processes on its own schedule, and it was
    measured lagging a landed focus by up to 86 ms
    (tools/win_focus_pass.py). Reading it in the same breath as the ask makes
    a correct answer look like a failure on a slow machine. A focus that goes
    somewhere else still fails, however long it is given.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _keyboard_focus() == hwnd:
            return True
        time.sleep(0.01)
    return False


def _take_focus(hwnd):
    """set_focus() a control, or skip if the desktop would not give it up.

    A shared desktop can hand the foreground to something else at any moment,
    which is a reason to skip and never a reason to fail - the same rule the
    Notepad selection test above follows. A *wrong* answer still fails.
    """
    element = UIElement.from_hwnd(hwnd)
    if element is None:
        pytest.skip("UI Automation did not expose the control")
    element.set_focus()
    if not element.has_focus():
        pytest.skip("could not take keyboard focus (another app has it)")
    return element


class TestCompareElements:
    """IUIAutomation::CompareElements, slot 3.

    A new slot, pinned the way this file pins every other one: against
    handles Win32 already knows to be the same or different. Two pointers to
    one element are never pointer-equal, so nothing here can be satisfied by
    comparing addresses - and a slot that silently called something else would
    answer the same thing for every pair.
    """

    def test_one_element_fetched_twice_compares_equal(self, automation):
        hwnd = user32.GetDesktopWindow()
        first, again = UIElement.from_hwnd(hwnd), UIElement.from_hwnd(hwnd)
        assert first._ptr.value != again._ptr.value            # two pointers...
        assert _same_element(first._ptr, again._ptr) is True   # ...one element

    def test_two_different_windows_do_not(self, automation):
        desktop = UIElement.from_hwnd(user32.GetDesktopWindow())
        foreground = UIElement.from_hwnd(user32.GetForegroundWindow())
        if foreground is None:
            pytest.skip("no foreground window")
        assert _same_element(desktop._ptr, foreground._ptr) is False

    def test_an_unanswerable_comparison_is_none_not_false(self, automation):
        # None, not False: "could not compare" and "different" have to stay
        # apart, or set_focus() reads a failed call as a non-landing.
        desktop = UIElement.from_hwnd(user32.GetDesktopWindow())
        assert _same_element(desktop._ptr, None) is None


class TestSetFocusIsAnActAndHasFocusIsTheQuestion:
    """set_focus() asks; has_focus() and contains_focus() answer.

    set_focus() used to return whether SetFocus was *accepted* - `== S_OK` -
    which is a different question: providers answer S_OK for elements that
    never take the keyboard (measured with tools/win_focus_pass.py, and pinned
    against a real application by the Notepad class at the end of this file).
    It now returns nothing at all, because there are two honest answers and
    this layer does not choose between them - `keyhac.core.fill` does.
    Everything here is cross-checked against GetGUIThreadInfo(0).hwndFocus
    rather than against UI Automation's own opinion of itself.
    """

    def test_set_focus_answers_nothing(self, focus_probe):
        # Falsy on purpose: a caller that forgets to check the focus fails
        # closed, where the old bool made it fail open.
        assert UIElement.from_hwnd(focus_probe["edit"]).set_focus() is None

    def test_focusing_a_control_lands_and_win32_agrees(self, focus_probe):
        _take_focus(focus_probe["edit"])
        assert _focus_settles_on(focus_probe["edit"])

    def test_the_answer_tracks_the_focus_rather_than_being_a_constant_yes(
            self, focus_probe):
        # The read-back on its own: an element nobody asked must report that it
        # does not hold the focus, however focusable it is, and must flip when
        # the focus really moves.
        _take_focus(focus_probe["edit"])
        other = UIElement.from_hwnd(focus_probe["other"])
        assert other.has_focus() is False
        _take_focus(focus_probe["other"])
        assert other.has_focus() is True
        assert _focus_settles_on(focus_probe["other"])

    def test_a_focus_inside_the_element_counts_as_landing_on_it(self, focus_probe):
        """The combo box: one control to a config, several elements to UIA.

        Focusing it lands the focus on its Edit child, which leaves the
        ComboBox's own HasKeyboardFocus False, and has_focus() False with it -
        so an action writes to the Edit child, which the tree shows with an
        AutomationId of its own. contains_focus() is how the two questions
        stay apart, and core uses it only to say so in the error.
        """
        _take_focus(focus_probe["edit"])
        combo = UIElement.from_hwnd(focus_probe["combo"])
        combo.set_focus()
        if not combo.contains_focus():
            pytest.skip("could not take keyboard focus (another app has it)")
        assert not _focus_settles_on(focus_probe["combo"], timeout=0.3)  # a child got it
        focused = _keyboard_focus()
        assert int(user32.GetAncestor(focused, GA_ROOT)) == focus_probe["window"]
        assert combo.get_attribute_value("HasKeyboardFocus") is False
        assert combo.has_focus() is False        # strictly, it does not
        assert combo.contains_focus() is True    # but the focus is in it

    def test_a_container_of_the_focused_element_does_not_claim_the_focus(
            self, focus_probe):
        """The other half of the combo box rule, and the dangerous half.

        Everything that *contains* the focused control also contains it: the
        window, and the desktop above that. Answering "the focus is inside me"
        for those would let set_text() type into a field the action never
        named - measured in a page, where the div around a focused input, its
        document and three panes above it all said yes. contains_focus() is
        true of every one of them, which is exactly why the platform layer
        answers both questions and lets fill decide which one it is entitled
        to act on.
        """
        _take_focus(focus_probe["edit"])
        window = UIElement.from_hwnd(focus_probe["window"])
        assert window.get_attribute_value("ControlType") == "Window"
        assert window.has_focus() is False
        assert window.contains_focus() is True     # and this is the trap
        desktop = UIElement.from_hwnd(user32.GetDesktopWindow())
        assert desktop.has_focus() is False
        assert desktop.contains_focus() is True    # true even of the desktop

    def test_a_label_is_not_a_delegating_type_either(self, focus_probe):
        # A Text element cannot take the keyboard, and it is not made to look
        # as though it had by whatever holds it.
        _take_focus(focus_probe["edit"])
        label = UIElement.from_hwnd(focus_probe["label"])
        assert label.get_attribute_value("ControlType") == "Text"
        assert label.has_focus() is False
        assert label.contains_focus() is False

    def test_an_element_whose_window_is_gone_reports_no_focus(self, automation):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.CreateWindowExW.restype = ctypes.c_void_p
        hwnd = user32.CreateWindowExW(
            0, "STATIC", "keyhac stale focus", 0x00CF0000, 40, 40, 200, 120,
            None, None, kernel32.GetModuleHandleW(None), None)
        element = UIElement.from_hwnd(hwnd)
        user32.DestroyWindow(hwnd)
        element.set_focus()
        assert element.has_focus() is False
        assert element.contains_focus() is False


@pytest.mark.slow
class TestSetFocusAgainstNotepadsWinUIStatusBar:
    """The lie, on a real application.

    Windows 11 ships Notepad as WinUI: its status bar is Text elements that
    share the window HWND and cannot take the keyboard. `SetFocus` on one
    returns S_OK anyway - so the old `== S_OK` answer was True for an element
    the focus never reached, and fill.set_text() would have typed into
    whatever did have it.

    Skips on a machine whose Notepad is the classic Win32 one, which has no
    such elements. It opens a window of its own, reads it, and closes it -
    nothing is typed anywhere.
    """

    @pytest.fixture
    def status_text(self, automation):
        import subprocess
        import time
        from keyhac.platform.win.uielement import (
            _control_view_walker, _element_out, _IUIAutomationTreeWalker)

        before = _notepad_hwnds()
        try:
            process = subprocess.Popen(["notepad.exe"])
        except OSError:
            pytest.skip("notepad.exe unavailable")
        window = None
        try:
            for _ in range(40):
                time.sleep(0.1)
                fresh = _notepad_hwnds() - before
                if fresh:
                    window = fresh.pop()
                    break
            if window is None:
                pytest.skip("Notepad did not open a window of its own")
            time.sleep(1.0)

            def walk(element, depth=0):
                found = []
                if depth > 8:
                    return found
                # By AutomationId, not by name: the status bar is localised,
                # and the machine this was written on runs Notepad in Japanese.
                if (element.get_attribute_value("ControlType") == "Text"
                        and element.get_attribute_value("AutomationId")
                        == "ContentTextBlock"):
                    found.append(element)
                walker = _control_view_walker()
                child = _element_out(walker,
                                     _IUIAutomationTreeWalker.GetFirstChildElement,
                                     ctypes.c_void_p(element._ptr.value))
                while child:
                    node = UIElement(child)
                    found += walk(node, depth + 1)
                    child = _element_out(
                        walker, _IUIAutomationTreeWalker.GetNextSiblingElement,
                        ctypes.c_void_p(node._ptr.value))
                return found

            texts = walk(UIElement.from_hwnd(window))
            if not texts:
                pytest.skip("no WinUI status-bar text (classic Notepad?)")
            yield texts[0]
        finally:
            if window is not None:
                _close_window(window)
            process.terminate()

    def test_setfocus_is_accepted_and_the_focus_does_not_land(self, status_text):
        from keyhac.platform.win import uielement as module

        # The old answer, asked directly: the request is accepted.
        hr = module._com_call(status_text._ptr,
                              module._IUIAutomationElement.SetFocus,
                              ctypes.c_long, [])
        assert hr == 0                       # S_OK
        # The new one: it did not land, and now that is what comes back.
        status_text.set_focus()
        assert status_text.has_focus() is False
        assert status_text.contains_focus() is False
        assert status_text.get_attribute_value("HasKeyboardFocus") is False
