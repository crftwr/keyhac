"""Live measurement of Windows set_focus() truthfulness (Windows only).

    python tools/win_focus_pass.py

THE QUESTION.  `keyhac/platform/win/uielement.py`'s `set_focus()` returns
whether `IUIAutomationElement::SetFocus` returned S_OK - whether the call was
*accepted*.  Its macOS twin reads the focus back and returns whether it
actually *landed*.  If acceptance is not landing, `keyhac.core.fill.set_text()`
writes while the focus is elsewhere and the keystrokes go to the window behind,
and the FOCUS_TIMEOUT retry loop never engages because the first ask already
answered True.

Inference is not measurement, hence this pass.

HOW IT ANSWERS.  Against Win32, never against UIA alone - the rule
tests/test_win_focus.py states, and the one that caught two wrong vtable slots
in that file.  The keyboard focus, system-wide, is
`GetGUIThreadInfo(0).hwndFocus`: the focused window of the *foreground*
thread's queue.  `GetFocus()` answers only for the calling thread and is
reported here to show why it is not the tool for this.  UIA's own answers - the
SetFocus HRESULT, `HasKeyboardFocus`, `GetFocusedElement` - are printed beside
it, and disagreement is the finding.

WHAT IT DRIVES.  Its own throwaway windows, spawned as child processes with
their own message loops (uia_pass.py's `CHILD_WINDOW_SOURCE`, grown a few
controls).  Nothing here touches an application the operator has open: focus
work moves the foreground around and injects an Alt tap, which is not
something to do to somebody's editor.

WHAT IT FOUND (2026-09-04, Windows 11 Home 10.0.26200; the long version is in
doc/dev/testing.md).  Every HWND-backed case lands - foreground, background,
asked without foreground rights, disabled, clipped, minimized, a label, a tab
item.  The old `== S_OK` answer was nevertheless wrong, but only where the
provider is not the HWND proxy: in an Edge page a `<div>` and a `<p>` accepted
SetFocus with S_OK while the focus stayed in the field that already had it,
and Notepad's WinUI status bar does the same (which is what
tests/test_win_focus.py pins).  Reading HasKeyboardFocus instead was measured
and rejected - a page element in a background browser reports True while the
keyboard is elsewhere - so the verdict comes from GetFocusedElement, and
set_focus() no longer gives one at all: has_focus() and contains_focus() do,
because "on it" and "inside it" are different questions with different
consequences.

Paste the output back verbatim.  A wrong slot shows up here as a plausible
wrong answer, not as an error.
"""

import sys

if sys.platform != "win32":
    sys.exit(f"{__file__} is a Windows pass; this is {sys.platform}.")

import ctypes                                                        # noqa: E402
import os                                                            # noqa: E402
import subprocess                                                    # noqa: E402
import threading                                                     # noqa: E402
import time                                                          # noqa: E402
from ctypes import wintypes                                          # noqa: E402

from keyhac.platform.win.uielement import UIElement, get_automation   # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Mandatory on 64-bit: ctypes defaults restype to c_int, which truncates a
# pointer-sized HWND to 32 bits into a handle that looks plausible and matches
# nothing.  Same trap keyhac/platform/win/focus.py calls out.
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetFocus.argtypes = []
user32.GetFocus.restype = ctypes.c_void_p
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte,
                               wintypes.DWORD, ctypes.c_void_p]
user32.SendMessageTimeoutW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

WM_SETTEXT, WM_GETTEXT = 0x000C, 0x000D
SMTO_ABORTIFHUNG = 0x0002

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


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

SW_SHOWNOACTIVATE, SW_SHOW, SW_MINIMIZE, SW_RESTORE = 4, 5, 6, 9

#: How much later GetGUIThreadInfo may notice a focus that has already landed.
#: Measured at 23-86 ms against a control in the foreground window, which is
#: the observer lagging and not the focus being late - a keystroke injected in
#: that window still reaches the target.
OBSERVER_LAG = 120.0

#: How long section I waits for UIA to answer about a non-pumping window
#: before calling it a hang.  Measured at "longer than 20 s", so this is only
#: how much of the operator's time the finding is worth.
HUNG_WATCHDOG = 6.0

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def note(text):
    print(f"       {text}")


def section(title):
    print(f"\n=== {title} ===")


# -- the Win32 answer --------------------------------------------------------

def keyboard_focus():
    """The HWND with keyboard focus, system-wide, or 0.

    Thread id 0 means "the foreground thread", which is the only thread whose
    focus window is the one keystrokes actually reach.  This is the answer
    everything below is graded against.
    """
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


#: hwnd -> the name this pass calls it, filled in as the probes come up.  A
#: run without it reads as a wall of handles in which no two lines can be
#: compared by eye, which is how the first run of this pass mistook a child
#: window for a foreground one.
NAMES = {}


def describe_hwnd(hwnd):
    if not hwnd:
        return "<none>"
    cls = ctypes.create_unicode_buffer(128)
    txt = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    user32.GetWindowTextW(hwnd, txt, 128)
    root = int(user32.GetAncestor(hwnd, GA_ROOT) or 0)
    where = NAMES.get(hwnd, f"{cls.value}({txt.value!r})")
    if root and root != hwnd:
        where += f" [child of {NAMES.get(root, 'unknown')}]"
    return f"{where} 0x{hwnd:x}"


def activate(hwnd, settle=0.35):
    """Bring a window to the foreground, and mean it.

    SetForegroundWindow is refused outright when the calling process does not
    already own the foreground, so this does what every focus-stealing utility
    does: an Alt tap to release the foreground lock, then attach to the
    foreground thread's input queue so the call is made from a thread that is
    allowed to make it.
    """
    user32.keybd_event(0x12, 0, 0, None)
    user32.keybd_event(0x12, 0, 2, None)
    target = user32.GetWindowThreadProcessId(hwnd, None)
    current = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    mine = kernel32.GetCurrentThreadId()
    others = {target, current} - {mine, 0}
    for other in others:
        user32.AttachThreadInput(mine, other, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for other in others:
            user32.AttachThreadInput(mine, other, False)
    time.sleep(settle)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


# -- the windows this pass drives -------------------------------------------

CHILD_SOURCE = r"""
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
user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

title, left, show, self_activate, hang = (sys.argv[1], int(sys.argv[2]),
                                          int(sys.argv[3]), int(sys.argv[4]),
                                          int(sys.argv[5]))
proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
wc = WNDCLASSW()
wc.lpfnWndProc = proc
wc.hInstance = kernel32.GetModuleHandleW(None)
wc.lpszClassName = "KeyhacFocusProbe"
user32.RegisterClassW(ctypes.byref(wc))
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_BORDER, WS_TABSTOP = 0x40000000, 0x10000000, 0x00800000, 0x00010000
ES_AUTOHSCROLL = 0x0080
hwnd = user32.CreateWindowExW(0, "KeyhacFocusProbe", title, WS_OVERLAPPEDWINDOW,
                              left, 80, 460, 220, None, None, wc.hInstance, None)
def edit(text, x, y):
    return user32.CreateWindowExW(
        0, "EDIT", text,
        WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL,
        x, y, 200, 26, hwnd, None, wc.hInstance, None)
normal = edit("normal", 20, 20)
disabled = edit("disabled", 20, 60)
user32.EnableWindow(disabled, False)
# Far below the client area, so it is clipped out of view without any
# scrolling machinery - the cheapest way to a control UIA calls offscreen.
clipped = edit("clipped", 20, 4000)
button = user32.CreateWindowExW(0, "BUTTON", "Probe Button",
                                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                                250, 20, 140, 30, hwnd, None, wc.hInstance, None)
# A label: an HWND that is not a focusable control.  UIA calls it Text.
label = user32.CreateWindowExW(0, "STATIC", "Probe Label", WS_CHILD | WS_VISIBLE,
                               250, 60, 140, 24, hwnd, None, wc.hInstance, None)
# A tab control, for the case that has no HWND at all: its TabItems are UIA
# elements inside the one control window, which is where "which element has
# the focus" stops being a question Win32 can answer.
class INITCOMMONCONTROLSEX(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("dwICC", wintypes.DWORD)]
class TCITEMW(ctypes.Structure):
    _fields_ = [("mask", wintypes.UINT), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("pszText", wintypes.LPWSTR),
                ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
                ("lParam", wintypes.LPARAM)]
comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
icc = INITCOMMONCONTROLSEX(ctypes.sizeof(INITCOMMONCONTROLSEX), 0x08)
comctl32.InitCommonControlsEx(ctypes.byref(icc))
tabs = user32.CreateWindowExW(0, "SysTabControl32", None,
                              WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                              20, 100, 380, 90, hwnd, None, wc.hInstance, None)
for index, text in enumerate(("Alpha", "Beta", "Gamma")):
    item = TCITEMW(mask=0x0001, pszText=text)
    user32.SendMessageW(tabs, 0x133E, index, ctypes.byref(item))
# A combo box: one element to an action, several to UIA, and the focus lands
# on the child.  Which is the case that decides whether "the focused element
# is this one" may be read strictly.
CBS_DROPDOWN, CBS_HASSTRINGS = 0x0002, 0x0200
combo = user32.CreateWindowExW(0, "COMBOBOX", None,
                               WS_CHILD | WS_VISIBLE | WS_TABSTOP
                               | CBS_DROPDOWN | CBS_HASSTRINGS,
                               250, 100, 150, 200, hwnd, None, wc.hInstance, None)
for text in ("One", "Two"):
    user32.SendMessageW(combo, 0x0143, 0,  # CB_ADDSTRING
                        ctypes.cast(ctypes.create_unicode_buffer(text),
                                    ctypes.c_void_p))
user32.ShowWindow(hwnd, show)
if self_activate:
    # The window takes the foreground *itself*, and injects the input event
    # that earns the right to.  That leaves this process holding the
    # foreground lock and the pass's own process without it - which is the
    # position keyhac is in when an action fires, and the position the pass
    # cannot arrange by asking nicely from outside.
    user32.keybd_event(0x12, 0, 0, None)
    user32.keybd_event(0x12, 0, 2, None)
    user32.SetForegroundWindow(hwnd)
print(" ".join(str(int(h)) for h in (hwnd, normal, disabled, clipped, button,
                                    label, tabs, combo)), flush=True)
if hang:
    # A window whose thread never pumps: "not responding", the state every
    # real application reaches now and then, and the one where a posted focus
    # change has nowhere to be processed.
    import time as _time
    while True:
        _time.sleep(1)
msg = ctypes.create_string_buffer(48)
while user32.GetMessageW(msg, None, 0, 0) > 0:
    user32.TranslateMessage(msg)
    user32.DispatchMessageW(msg)
"""


class Probe:
    """One throwaway window, in its own process, with a message loop."""

    def __init__(self, title, left, show, self_activate=False, hang=False):
        self.process = subprocess.Popen(
            [sys.executable, "-c", CHILD_SOURCE, title, str(left), str(show),
             str(int(self_activate)), str(int(hang))],
            stdout=subprocess.PIPE, text=True)
        line = self.process.stdout.readline().split()
        (self.hwnd, self.normal, self.disabled, self.clipped, self.button,
         self.label, self.tabs, self.combo) = (int(h) for h in line)
        self.title = title
        short = title.rsplit("-", 1)[-1].lower()
        NAMES.update({self.hwnd: f"{short}.window", self.normal: f"{short}.normal",
                      self.disabled: f"{short}.disabled",
                      self.clipped: f"{short}.clipped",
                      self.button: f"{short}.button",
                      self.label: f"{short}.label",
                      self.tabs: f"{short}.tabs",
                      self.combo: f"{short}.combo"})
        time.sleep(0.25)

    def hwnds(self):
        return (self.hwnd, self.normal, self.disabled, self.clipped,
                self.button, self.label, self.tabs, self.combo)

    def close(self):
        self.process.terminate()


# -- one measurement ---------------------------------------------------------

#: How long a landing is given before it is called a non-landing.  Generous on
#: purpose: the finding this pass is after is "never", and anything short would
#: turn the timing gap into a false one.
SETTLE = 1.0


def focused_element_hwnd():
    """The HWND behind UIA's GetFocusedElement, or 0."""
    focused = UIElement.from_focus()
    if focused is None:
        return 0
    return int(focused.get_attribute_value("NativeWindowHandle") or 0)


def measure(label, hwnd, expect_landing):
    """Ask UIA for the focus, then ask Win32 where it went - twice.

    Immediately, and again after polling for SETTLE, because the answers are
    not the same question.  `set_text()` writes on the immediate one; a
    landing that only shows up later is the shape of the macOS bug, not an
    absence of one.

    Returns (accepted, landed_now, landed_at) in milliseconds, or None for a
    landing that never came.
    """
    element = UIElement.from_hwnd(hwnd)
    if element is None:
        check(label, False, "no UIA element for the control")
        return None, None, None
    before = keyboard_focus()
    started = time.perf_counter()
    element.set_focus()
    accepted = bool(element.has_focus())
    immediate = keyboard_focus()
    flag_now = element.get_attribute_value("HasKeyboardFocus")
    uia_now = focused_element_hwnd()
    landed_at = 0.0 if immediate == hwnd else None
    while landed_at is None and (time.perf_counter() - started) < SETTLE:
        time.sleep(0.005)
        if keyboard_focus() == hwnd:
            landed_at = (time.perf_counter() - started) * 1000
    settled = keyboard_focus()
    print(f"\n-- {label}")
    note(f"has_focus() after the ask       : {accepted}")
    note(f"  has_focus() / contains_focus()     : "
         f"{element.has_focus()} / {element.contains_focus()}")
    note(f"keyboard focus before the ask       : {describe_hwnd(before)}")
    note(f"keyboard focus immediately after    : {describe_hwnd(immediate)}")
    note(f"keyboard focus after {SETTLE:g}s of polling: {describe_hwnd(settled)}")
    note("landed after                        : "
         + ("never" if landed_at is None else f"{landed_at:.0f} ms"))
    note(f"GetForegroundWindow()               : "
         f"{describe_hwnd(int(user32.GetForegroundWindow() or 0))}")
    note(f"GetFocus() on this thread           : "
         f"{describe_hwnd(int(user32.GetFocus() or 0))}")
    note(f"UIA HasKeyboardFocus (immediate)    : {flag_now}")
    note(f"UIA HasKeyboardFocus (settled)      : "
         f"{element.get_attribute_value('HasKeyboardFocus')}")
    note(f"UIA GetFocusedElement (immediate)   : {describe_hwnd(uia_now)}")
    note(f"UIA GetFocusedElement (settled)     : {describe_hwnd(focused_element_hwnd())}")
    landed = landed_at is not None
    check(f"{label}: the verdict agrees with Win32",
          accepted == landed,
          f"reported {accepted}, keyboard focus "
          + ("landed" if landed else f"DID NOT LAND in {SETTLE:g}s"))
    if expect_landing is not None:
        check(f"{label}: focus landed as expected", landed == expect_landing,
              f"expected {expect_landing}, got {landed}")
    return accepted, immediate == hwnd, landed_at


def control_text(hwnd, timeout=500):
    """A control's text, without hanging on a window that is not pumping."""
    buf = ctypes.create_unicode_buffer(256)
    result = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, ctypes.c_void_p(256),
                                    ctypes.cast(buf, ctypes.c_void_p),
                                    SMTO_ABORTIFHUNG, timeout,
                                    ctypes.byref(result))
    return buf.value if ok else "<no answer>"


def set_control_text(hwnd, text):
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_SETTEXT, None,
                               ctypes.cast(ctypes.create_unicode_buffer(text),
                                           ctypes.c_void_p),
                               SMTO_ABORTIFHUNG, 500, ctypes.byref(result))


def report():
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name in failed:
        print(f"  FAIL  {name}")
    return 1 if failed else 0


def main():
    if get_automation() is None:
        sys.exit("UI Automation unavailable on this machine")

    back = Probe("KEYHAC-FOCUS-PASS-BACK", 60, SW_SHOWNOACTIVATE)
    front = Probe("KEYHAC-FOCUS-PASS-FRONT", 560, SW_SHOW)
    probes = [back, front]
    try:
        section("staging")
        check("the front window is foreground", activate(front.hwnd),
              describe_hwnd(int(user32.GetForegroundWindow() or 0)))
        check("Win32 says the keyboard focus is inside it",
              keyboard_focus() in front.hwnds(), describe_hwnd(keyboard_focus()))

        section("0. CompareElements (IUIAutomation slot 3), pinned")
        # A new slot, and a wrong one does not raise - it calls a different
        # method.  Pinned against handles Win32 already knows are different:
        # two fetches of the same HWND must compare same, two different HWNDs
        # must not, and the answer must not be "same" for everything (which is
        # what a slot returning S_OK and touching nothing would look like).
        from keyhac.platform.win.uielement import _same_element
        first = UIElement.from_hwnd(front.normal)
        again = UIElement.from_hwnd(front.normal)
        other = UIElement.from_hwnd(front.button)
        check("CompareElements: the same HWND, fetched twice, is one element",
              _same_element(first._ptr, again._ptr) is True)
        check("CompareElements: two different controls are not",
              _same_element(first._ptr, other._ptr) is False)
        check("CompareElements: and it is not pointer identity being tested",
              first._ptr.value != again._ptr.value,
              "two pointers, one element - which is why the call is needed")

        section("A. a control in the FOREGROUND window (the control case)")
        measure("foreground edit", front.normal, expect_landing=True)

        section("B. a control in a window that is NOT foreground")
        activate(front.hwnd)
        measure("background window's edit", back.normal, expect_landing=None)
        note(f"foreground after the ask: "
             f"{describe_hwnd(int(user32.GetForegroundWindow() or 0))}")

        section("B2. the same ask, from a process with no foreground rights")
        # B was rigged and had to be: activate() injects an Alt tap, which
        # makes this process the source of the last input event and so a
        # process Windows lets change the foreground.  Take that away - let
        # the owner window claim the foreground itself - and the ask is the
        # one keyhac actually makes.
        owner = Probe("KEYHAC-FOCUS-PASS-OWNER", 560, SW_SHOW, self_activate=True)
        probes.append(owner)
        time.sleep(0.5)
        note(f"foreground before the ask: "
             f"{describe_hwnd(int(user32.GetForegroundWindow() or 0))}")
        check("B2 is staged: the owner window holds the foreground, unaided",
              int(user32.GetForegroundWindow() or 0) == owner.hwnd)
        measure("background edit, no foreground rights", back.normal,
                expect_landing=None)

        section("C. a DISABLED control in the foreground window")
        activate(front.hwnd)
        measure("disabled edit", front.disabled, expect_landing=None)

        section("D. an OFFSCREEN (clipped) control in the foreground window")
        activate(front.hwnd)
        clipped = UIElement.from_hwnd(front.clipped)
        note(f"UIA IsOffscreen: {clipped.get_attribute_value('IsOffscreen')}")
        measure("clipped edit", front.clipped, expect_landing=None)

        section("E. a control in a MINIMIZED window")
        activate(front.hwnd)
        user32.ShowWindow(back.hwnd, SW_MINIMIZE)
        time.sleep(0.3)
        measure("minimized window's edit", back.normal, expect_landing=None)
        user32.ShowWindow(back.hwnd, SW_RESTORE)
        time.sleep(0.3)

        section("F. timing - asked in the same breath as the window comes front")
        # The macOS bug was a 121 ms gap, so the Windows one may be timing too.
        activate(front.hwnd)
        user32.keybd_event(0x12, 0, 0, None)
        user32.keybd_event(0x12, 0, 2, None)
        user32.SetForegroundWindow(back.hwnd)          # no settling wait
        element = UIElement.from_hwnd(back.normal)
        started = time.perf_counter()
        element.set_focus()
        accepted = bool(element.has_focus())
        asked_at = (time.perf_counter() - started) * 1000
        landed_at = flag_at = None
        while (time.perf_counter() - started) < 2.0:
            elapsed = (time.perf_counter() - started) * 1000
            if landed_at is None and keyboard_focus() == back.normal:
                landed_at = elapsed
            if flag_at is None and element.get_attribute_value("HasKeyboardFocus"):
                flag_at = elapsed
            if landed_at is not None and flag_at is not None:
                break
            time.sleep(0.005)
        note(f"has_focus() said {accepted} after {asked_at:.0f} ms")
        note("Win32 focus landed after      : "
             + ("never (2 s)" if landed_at is None else f"{landed_at:.0f} ms"))
        note("HasKeyboardFocus turned true  : "
             + ("never (2 s)" if flag_at is None else f"{flag_at:.0f} ms"))
        # Not "within 5 ms": GetGUIThreadInfo is an observer that was measured
        # trailing a landed focus by up to 86 ms, so the property to hold is
        # that set_focus() does not answer *before* the focus is there.
        check("F: the verdict does not come ahead of the landing",
              landed_at is not None and landed_at <= asked_at + OBSERVER_LAG,
              f"answered at {asked_at:.0f} ms, "
              + ("never landed" if landed_at is None else f"landed at {landed_at:.0f} ms"))

        section("G. does HasKeyboardFocus lie when the window is in the background?")
        # The question macOS's docstring asks: an element can hold its own
        # application's focus while the keyboard goes somewhere else.
        activate(back.hwnd)
        UIElement.from_hwnd(back.normal).set_focus()
        time.sleep(0.3)
        activate(front.hwnd)
        time.sleep(0.3)
        background = UIElement.from_hwnd(back.normal)
        flag = background.get_attribute_value("HasKeyboardFocus")
        focused = UIElement.from_focus()
        focused_hwnd = focused.get_attribute_value("NativeWindowHandle") if focused else 0
        note(f"keyboard focus now              : {describe_hwnd(keyboard_focus())}")
        note(f"background edit HasKeyboardFocus: {flag}")
        note(f"GetFocusedElement's HWND        : {describe_hwnd(int(focused_hwnd or 0))}")
        check("G: HasKeyboardFocus is false for the background element",
              flag is False,
              "true here would mean HasKeyboardFocus alone is not enough")

        section("H. the consequence - a keystroke sent on set_focus()'s word")
        # The whole point of the question.  fill.set_text() pastes as soon as
        # focus() says True; if the focus is still on its way, the keystroke
        # goes to whatever holds it now - the window behind.
        set_control_text(front.normal, "")
        set_control_text(back.normal, "")
        activate(front.hwnd)
        UIElement.from_hwnd(front.normal).set_focus()
        time.sleep(0.4)
        check("H is staged: the front edit holds the focus",
              keyboard_focus() == front.normal, describe_hwnd(keyboard_focus()))
        target = UIElement.from_hwnd(back.normal)
        target.set_focus()
        accepted = bool(target.has_focus())
        # No wait at all: this is set_text()'s own sequence, focus then write.
        user32.keybd_event(0x58, 0, 0, None)          # 'X'
        user32.keybd_event(0x58, 0, 2, None)
        time.sleep(0.6)
        went_to_target = control_text(back.normal)
        went_to_behind = control_text(front.normal)
        note(f"has_focus() said            : {accepted}")
        note(f"the target edit now holds   : {went_to_target!r}")
        note(f"the window behind now holds : {went_to_behind!r}")
        check("H: the keystroke reached the element has_focus() reported",
              went_to_target.upper() == "X" and went_to_behind == "",
              "a character in the window behind is the bug this pass is for")

        section("J. a LABEL - an HWND that is not a focusable control")
        activate(front.hwnd)
        UIElement.from_hwnd(front.normal).set_focus()
        time.sleep(0.4)
        note(f"focus parked on            : {describe_hwnd(keyboard_focus())}")
        label = UIElement.from_hwnd(front.label)
        note(f"the label is UIA           : {label.get_attribute_value('ControlType')}"
             f" {label.get_attribute_value('Name')!r}")
        measure("label", front.label, expect_landing=None)

        section("K. an element with NO HWND of its own - a tab item")
        # Where Win32 stops being able to answer: the three tab items live
        # inside one control window, so GetGUIThreadInfo can only ever name
        # the tab control.  Which element inside it has the focus is a
        # question only UIA can answer - and the question set_focus() has to
        # answer honestly for elements like this one, since most of a modern
        # UI (WinUI, Electron, a browser page) is made of them.
        activate(front.hwnd)
        UIElement.from_hwnd(front.normal).set_focus()
        time.sleep(0.4)
        check("K is staged: the focus is on the edit, not the tabs",
              keyboard_focus() == front.normal, describe_hwnd(keyboard_focus()))
        items = UIElement.from_hwnd(front.tabs).children()
        note(f"tab items                  : "
             f"{[i.get_attribute_value('Name') for i in items]}")
        item = items[1]
        item.set_focus()
        accepted = bool(item.has_focus())
        time.sleep(0.4)
        focused = UIElement.from_focus()
        kind = focused.get_attribute_value("ControlType") if focused else None
        name = focused.get_attribute_value("Name") if focused else None
        note(f"has_focus() on the tab item: {accepted}")
        note(f"keyboard focus (Win32)     : {describe_hwnd(keyboard_focus())}")
        note(f"the item's HasKeyboardFocus: "
             f"{item.get_attribute_value('HasKeyboardFocus')}")
        note(f"GetFocusedElement          : {kind} {name!r} "
             f"hwnd={describe_hwnd(focused_element_hwnd())}")
        landed = kind == "TabItem" and name == item.get_attribute_value("Name")
        check("K: has_focus() agrees with what UIA says has the focus",
              accepted == landed,
              f"reported {accepted}, GetFocusedElement "
              + ("is the item" if landed else "is something else"))

        section("L. a COMBO BOX - one control to an action, several to UIA")
        # Whether "GetFocusedElement is this element" may be read strictly.
        # A combo box is one thing to the action that names it and a small
        # tree to UIA, and the focus goes to a child.
        activate(front.hwnd)
        UIElement.from_hwnd(front.normal).set_focus()
        time.sleep(0.4)
        combo = UIElement.from_hwnd(front.combo)
        combo.set_focus()
        accepted = bool(combo.has_focus())
        time.sleep(0.4)
        focused = UIElement.from_focus()
        note(f"combo.has_focus()          : {accepted}")
        note(f"combo.contains_focus()     : {combo.contains_focus()}")
        note(f"the combo is               : "
             f"{combo.get_attribute_value('ControlType')}")
        note(f"Win32 keyboard focus       : {describe_hwnd(keyboard_focus())}")
        note(f"combo's HasKeyboardFocus   : "
             f"{combo.get_attribute_value('HasKeyboardFocus')}")
        if focused is not None:
            note(f"GetFocusedElement          : "
                 f"{focused.get_attribute_value('ControlType')} "
                 f"{focused.get_attribute_value('Name')!r} "
                 f"hwnd={describe_hwnd(focused_element_hwnd())}")
            parent = focused.parent()
            note("its parent                 : "
                 + (f"{parent.get_attribute_value('ControlType')} "
                    f"hwnd={describe_hwnd(int(parent.get_attribute_value('NativeWindowHandle') or 0))}"
                    if parent is not None else "<none>"))
        check("L: the combo does not hold the focus, its Edit part does",
              accepted is False and combo.contains_focus() is True,
              describe_hwnd(keyboard_focus()))
        # And the part is a target an action can name: it is in the tree with
        # an AutomationId of its own, and writing to it reads back on both.
        inner = [c for c in combo.children()
                 if c.get_attribute_value("ControlType") == "Edit"]
        note("the Edit part                : "
             + (f"AutomationId="
                f"{inner[0].get_attribute_value('AutomationId')!r}"
                if inner else "<not in the tree>"))
        if inner:
            inner[0].set_focus()
            check("L: ...and the part takes the focus strictly",
                  inner[0].has_focus() is True)

        section("I. a control whose thread never pumps (a hung application)")
        # Behind a watchdog, because the answer turned out to be that there is
        # no answer: it is not set_focus() that lies here, it is every UIA
        # call into a non-pumping provider that never returns.  A first run of
        # this section took the whole pass down with it.
        hung = Probe("KEYHAC-FOCUS-PASS-HUNG", 60, SW_SHOWNOACTIVATE, hang=True)
        probes.append(hung)
        activate(front.hwnd)
        answer = {}

        def ask_the_hung_one():
            started = time.perf_counter()
            element = UIElement.from_hwnd(hung.normal)
            answer["from_hwnd_ms"] = (time.perf_counter() - started) * 1000
            if element is None:
                return
            started = time.perf_counter()
            element.set_focus()
            answer["landed"] = bool(element.has_focus())
            answer["set_focus_ms"] = (time.perf_counter() - started) * 1000

        worker = threading.Thread(target=ask_the_hung_one, daemon=True)
        worker.start()
        worker.join(HUNG_WATCHDOG)
        if worker.is_alive():
            note(f"UIA never came back: still blocked after {HUNG_WATCHDOG:g}s")
            check("I: the hung window is a hang, not a lie", True,
                  "UIElement.from_hwnd blocks before set_focus is even reached")
        else:
            for key, value in answer.items():
                note(f"{key}: {value}")
            check("I: has_focus() agrees with Win32 on the hung window",
                  answer.get("landed") is (keyboard_focus() == hung.normal),
                  describe_hwnd(keyboard_focus()))
    finally:
        for probe in probes:
            probe.close()
    code = report()
    # os._exit, because section I may leave a thread parked inside a COM call
    # that will not be interrupted and that ordinary shutdown waits on.
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
