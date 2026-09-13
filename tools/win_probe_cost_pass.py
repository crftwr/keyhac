"""What a focused-element read costs on Windows, per keystroke (Windows only).

    python tools/win_probe_cost_pass.py

THE QUESTION (issue #145).  `keyhac/platform/win/focus.py`'s cheap tier is
two HWNDs and a window title, and it runs on every key down *and* up.  It
cannot see focus moving *inside* a window, so `focus_path_pattern=` is blind
to a ListView item, a tab item, and to every focus move in a Chromium,
Electron, WPF or WinUI window - there is one HWND there and no focusable
children at all.

macOS can afford to put the focused element into its probe: one
`AXFocusedUIElement` read, measured at **0.107 ms**, plus a local `CFEqual`
to compare it with the previous one.  The Windows equivalent is
`IUIAutomation::GetFocusedElement()`, whose cost on its own has never been
measured, and the comparison is `IUIAutomation::CompareElements` - a second
cross-process call, because Windows has no local equivalent of `CFEqual`.

So the trade is: accuracy for `focus_path_pattern=`, paid for by putting the
first cross-process call into a tier that is today pure-local and cannot be
stalled by another application.  `GetWindowTextW` deliberately does not
message a window it does not own; UIA has no such promise.

HOW IT ANSWERS.  Warm per-call timing against four kinds of provider - a
classic Win32 window, Chromium, WinUI/XAML, and a window whose thread pumps
slowly - plus the two cases that decide it rather than price it: a foreground
application that never pumps at all (behind a watchdog, because the answer
may be "never"), and an element whose process has since exited, which the
macOS probe survives because CFEqual is local.

Everything is measured against the same baseline: the cheap tier exactly as
`get_focus()` runs it today, in the same loop on the same machine, so the
numbers are a ratio and not just milliseconds.

WHAT IT DRIVES.  Its own throwaway windows, spawned as child processes, and
its own disposable Edge and Notepad windows - a blank Notepad and a local
scratch page.  Nothing here touches an application the operator has open:
measuring means moving the foreground around and injecting an Alt tap, which
is not something to do to somebody's editor.

Paste the output back verbatim.
"""

import sys

if sys.platform != "win32":
    sys.exit(f"{__file__} is a Windows pass; this is {sys.platform}.")

import ctypes                                                        # noqa: E402
import os                                                            # noqa: E402
import statistics                                                    # noqa: E402
import subprocess                                                    # noqa: E402
import tempfile                                                      # noqa: E402
import threading                                                     # noqa: E402
import time                                                          # noqa: E402
from ctypes import wintypes                                          # noqa: E402

from keyhac.platform.win.uielement import (                          # noqa: E402
    UIElement, get_automation, _com_call, _element_out, _release, _same_element,
    _IUIAutomation)

# Window titles come from whatever is in front, and a console codepage that
# cannot encode one must not be what ends the pass.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)

# Mandatory on 64-bit: ctypes defaults restype to c_int, which truncates a
# pointer-sized HWND to 32 bits into a handle that looks plausible and
# matches nothing.  Same trap keyhac/platform/win/focus.py calls out.
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte,
                               wintypes.DWORD, ctypes.c_void_p]
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.c_void_p
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p),
                ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p),
                ("hwndCaret", ctypes.c_void_p), ("rcCaret", wintypes.RECT)]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL

#: IUIAutomationElement::GetRuntimeId (UIAutomationClient.h), which
#: keyhac itself does not call - identity_key() returns None on Windows
#: because the control view is a real tree.  Pinned in section 0.
GET_RUNTIME_ID = 4

oleaut32.SafeArrayGetLBound.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.POINTER(ctypes.c_long)]
oleaut32.SafeArrayGetLBound.restype = ctypes.c_long
oleaut32.SafeArrayGetUBound.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.POINTER(ctypes.c_long)]
oleaut32.SafeArrayGetUBound.restype = ctypes.c_long
oleaut32.SafeArrayGetElement.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_long),
                                         ctypes.c_void_p]
oleaut32.SafeArrayGetElement.restype = ctypes.c_long
oleaut32.SafeArrayDestroy.argtypes = [ctypes.c_void_p]
oleaut32.SafeArrayDestroy.restype = ctypes.c_long

GA_ROOT = 2
SW_SHOW = 5
S_OK = 0
COINIT_APARTMENTTHREADED = 0x2

#: Samples per timed loop.  A cross-process call is milliseconds, so this is
#: under a second per target and still enough for a p95 to mean something.
SAMPLES = 200

#: How long the hung-application section waits for UIA to answer before
#: calling it a hang.  win_focus_pass.py measured the neighbouring call at
#: "longer than 20 s", so this is only how much of the operator's time the
#: finding is worth.
HUNG_WATCHDOG = 6.0

#: The macOS number this is being compared against: one AXFocusedUIElement
#: read, measured while answering issue #144.
MACOS_FOCUSED_READ_MS = 0.107

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def note(text):
    print(f"       {text}")


def section(title):
    print(f"\n=== {title} ===")


# -- Win32 ground truth ------------------------------------------------------

def keyboard_focus():
    """The HWND with keyboard focus, system-wide, or 0."""
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


def describe_hwnd(hwnd):
    if not hwnd:
        return "<none>"
    cls = ctypes.create_unicode_buffer(128)
    txt = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    user32.GetWindowTextW(hwnd, txt, 128)
    return f"{cls.value}({txt.value!r}) 0x{hwnd:x}"


def activate(hwnd, settle=0.4):
    """Bring a window to the foreground, and mean it.

    SetForegroundWindow is refused outright when the calling process does not
    already own the foreground, so this does what every focus-stealing
    utility does: an Alt tap to release the foreground lock, then attach to
    the foreground thread's input queue.
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


def top_window_of_pid(pid, timeout=20.0):
    """The visible top-level window of a process, waited for."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        found = []

        def visit(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                if int(user32.GetAncestor(hwnd, GA_ROOT) or 0) == int(hwnd):
                    length = user32.GetWindowTextW(hwnd, ctypes.create_unicode_buffer(4), 4)
                    if length:
                        found.append(int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(visit), 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return 0


# -- the windows this pass drives -------------------------------------------

CHILD_SOURCE = r"""
import ctypes, sys, time
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
user32.SetFocus.argtypes = [wintypes.HWND]
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

title, left, show, self_activate, hang, slow_ms = (
    sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]),
    int(sys.argv[5]), int(sys.argv[6]))

def wndproc(h, m, w, l):
    if slow_ms:
        # A window that pumps, but slowly: "responding, eventually", which is
        # the state a real application spends real time in.  WM_PAINT and the
        # mouse-move storm are left alone so the window still draws - what is
        # being slowed is the message traffic a UIA provider is serviced
        # behind.
        if m not in (0x000F, 0x0200, 0x0084, 0x0020):
            time.sleep(slow_ms / 1000.0)
    return user32.DefWindowProcW(h, m, w, l)

proc = WNDPROC(wndproc)
wc = WNDCLASSW()
wc.lpfnWndProc = proc
wc.hInstance = kernel32.GetModuleHandleW(None)
wc.lpszClassName = "KeyhacProbeCost"
user32.RegisterClassW(ctypes.byref(wc))
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_BORDER, WS_TABSTOP = 0x40000000, 0x10000000, 0x00800000, 0x00010000
ES_AUTOHSCROLL = 0x0080
hwnd = user32.CreateWindowExW(0, "KeyhacProbeCost", title, WS_OVERLAPPEDWINDOW,
                              left, 80, 420, 160, None, None, wc.hInstance, None)
normal = user32.CreateWindowExW(
    0, "EDIT", "normal",
    WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL,
    20, 20, 200, 26, hwnd, None, wc.hInstance, None)
user32.ShowWindow(hwnd, show)
user32.SetFocus(normal)
if self_activate:
    # The window takes the foreground itself, and injects the input event
    # that earns the right to - the only way to arrange a foreground window
    # in a process that is about to stop pumping.
    user32.keybd_event(0x12, 0, 0, None)
    user32.keybd_event(0x12, 0, 2, None)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(normal)
print(" ".join(str(int(h)) for h in (hwnd, normal)), flush=True)
if hang:
    # A window whose thread never pumps: "not responding", the state every
    # real application reaches now and then.
    while True:
        time.sleep(1)
msg = ctypes.create_string_buffer(48)
while user32.GetMessageW(msg, None, 0, 0) > 0:
    user32.TranslateMessage(msg)
    user32.DispatchMessageW(msg)
"""


class Probe:
    """One throwaway window, in its own process, with a message loop."""

    def __init__(self, title, left, show=SW_SHOW, self_activate=False,
                 hang=False, slow_ms=0):
        self.process = subprocess.Popen(
            [sys.executable, "-c", CHILD_SOURCE, title, str(left), str(show),
             str(int(self_activate)), str(int(hang)), str(int(slow_ms))],
            stdout=subprocess.PIPE, text=True)
        self.hwnd, self.normal = (int(h) for h in
                                  self.process.stdout.readline().split())
        self.title = title
        time.sleep(0.3)

    def close(self):
        self.process.terminate()


class AppProbe:
    """A disposable window of a real application, for its UIA provider."""

    def __init__(self, label, argv):
        self.label = label
        self.process = subprocess.Popen(argv)
        self.hwnd = top_window_of_pid(self.process.pid)
        if not self.hwnd:
            # Edge and Notepad both hand off to an already-running instance,
            # in which case the window belongs to a process that is not the
            # one just spawned.  Fall back to whatever came to the front.
            time.sleep(2.0)
            self.hwnd = int(user32.GetForegroundWindow() or 0)
        time.sleep(1.5)

    def close(self):
        try:
            self.process.terminate()
        except Exception:
            pass


# -- timing ------------------------------------------------------------------

#: A key event this slow is not a slow key event.  keyhac-win's hook has a
#: sanity check because Windows silently unhooks a hook that takes too long,
#: so the count of samples past this is part of the price, not an outlier to
#: be averaged away.
SLOW_EVENT_MS = 10.0


def timed(fn, samples=SAMPLES, warmup=5):
    """Warm per-call milliseconds: (median, p95, min, max, count over 10 ms)."""
    for _ in range(warmup):
        fn()
    taken = []
    for _ in range(samples):
        started = time.perf_counter()
        fn()
        taken.append((time.perf_counter() - started) * 1000)
    slow = sum(1 for value in taken if value > SLOW_EVENT_MS)
    taken.sort()
    return (statistics.median(taken), taken[int(len(taken) * 0.95) - 1],
            taken[0], taken[-1], slow)


def report_timing(label, stats, samples=SAMPLES):
    median, p95, low, high, slow = stats
    print(f"       {label:<38} median {median:8.3f} ms   p95 {p95:8.3f} ms"
          f"   min {low:8.3f}   max {high:8.3f}   >{SLOW_EVENT_MS:g}ms {slow:3d}"
          f"/{samples}")
    return median


# -- the calls being priced --------------------------------------------------

def win32_probe():
    """The cheap tier exactly as get_focus() runs it today."""
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return None
    focus_hwnd = foreground
    thread_id = user32.GetWindowThreadProcessId(foreground, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) and info.hwndFocus:
        focus_hwnd = info.hwndFocus
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(foreground, buffer, 256)
    return (int(foreground), int(focus_hwnd), buffer.value)


def focused_element_raw():
    """GetFocusedElement and Release, with no Python object around it."""
    automation = get_automation()
    ptr = _element_out(automation, _IUIAutomation.GetFocusedElement)
    _release(ptr)


def runtime_id(element):
    """GetRuntimeId as a tuple, or None.

    The other way to compare two elements: one property-shaped fetch instead
    of CompareElements' round trip.  Slot 4 is pinned by the sanity check in
    section 0 - a wrong slot here calls a different method and returns a
    plausible wrong answer rather than raising.
    """
    out = ctypes.c_void_p()
    hr = _com_call(element._ptr, GET_RUNTIME_ID, ctypes.c_long,
                   [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(out))
    if hr != S_OK or not out.value:
        return None
    try:
        low, high = ctypes.c_long(), ctypes.c_long()
        if oleaut32.SafeArrayGetLBound(out, 1, ctypes.byref(low)) != S_OK:
            return None
        if oleaut32.SafeArrayGetUBound(out, 1, ctypes.byref(high)) != S_OK:
            return None
        values = []
        for index in range(low.value, high.value + 1):
            item = ctypes.c_long()
            subscript = ctypes.c_long(index)
            if oleaut32.SafeArrayGetElement(out, ctypes.byref(subscript),
                                            ctypes.byref(item)) != S_OK:
                return None
            values.append(item.value)
        return tuple(values)
    finally:
        oleaut32.SafeArrayDestroy(out)


def describe_element(element):
    if element is None:
        return "<none>"
    return (f"{element.get_attribute_value('ControlType')}"
            f"({element.get_attribute_value('Name') or ''!r}) "
            f"framework={element.get_attribute_value('FrameworkId')}")


# -- one target --------------------------------------------------------------

def price_target(label, hwnd, samples=SAMPLES):
    """Every per-keystroke candidate, priced against this window in front."""
    section(f"{label}")
    if not activate(hwnd):
        check(f"{label}: window is foreground", False,
              describe_hwnd(int(user32.GetForegroundWindow() or 0)))
        return None
    note(f"foreground: {describe_hwnd(int(user32.GetForegroundWindow() or 0))}")
    note(f"Win32 focus: {describe_hwnd(keyboard_focus())}")

    element = UIElement.from_focus()
    if element is None:
        check(f"{label}: UIA names a focused element", False)
        return None
    note(f"UIA focus:   {describe_element(element)}")

    costs = {}
    costs["win32"] = report_timing("cheap tier today (2 HWNDs + title)",
                                   timed(win32_probe, samples), samples)
    costs["raw"] = report_timing("GetFocusedElement + Release (raw COM)",
                                 timed(focused_element_raw, samples), samples)
    costs["wrapped"] = report_timing("UIElement.from_focus()",
                                     timed(lambda: UIElement.from_focus(), samples),
                                     samples)

    previous = UIElement.from_focus()
    costs["compare"] = report_timing(
        "CompareElements (the identity check)",
        timed(lambda: _same_element(previous._ptr, element._ptr), samples), samples)
    if runtime_id(element) is not None:
        costs["runtime_id"] = report_timing(
            "GetRuntimeId (the alternative)",
            timed(lambda: runtime_id(element), samples), samples)

    def candidate():
        """The whole per-keystroke probe #144's design would need here."""
        current = UIElement.from_focus()
        if current is not None:
            _same_element(previous._ptr, current._ptr)

    costs["candidate"] = report_timing("=> candidate probe (read + compare)",
                                       timed(candidate, samples), samples)
    note(f"ratio to the cheap tier: "
         f"{costs['candidate'] / max(costs['win32'], 1e-6):,.0f}x"
         f"   |   to macOS's {MACOS_FOCUSED_READ_MS} ms read: "
         f"{costs['candidate'] / MACOS_FOCUSED_READ_MS:,.1f}x")
    return costs


def report():
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


def main():
    probes = []
    page = None
    try:
        section("0. GetRuntimeId (IUIAutomationElement slot 4), pinned")
        # A new slot, and a wrong one does not raise - it calls a different
        # method.  Pinned the way section 0 of win_focus_pass.py pins
        # CompareElements: against handles Win32 already knows are different.
        classic = Probe("KEYHAC-PROBE-COST-CLASSIC", 40)
        probes.append(classic)
        first = UIElement.from_hwnd(classic.normal)
        again = UIElement.from_hwnd(classic.normal)
        other = UIElement.from_hwnd(classic.hwnd)
        one, two, three = runtime_id(first), runtime_id(again), runtime_id(other)
        note(f"runtime ids: {one} {two} {three}")
        check("GetRuntimeId: the same HWND, fetched twice, has one id",
              one is not None and one == two)
        check("GetRuntimeId: two different windows do not", one != three)

        # -- the price -----------------------------------------------------
        priced = {}
        priced["classic Win32"] = price_target(
            "A. a classic Win32 window (EDIT control focused)", classic.hwnd)

        page_html = ("<!doctype html><title>keyhac probe cost</title>"
                     "<body><input autofocus size=40 "
                     "placeholder='keyhac probe cost'></body>")
        handle, page = tempfile.mkstemp(suffix=".html", prefix="keyhac_probe_")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(page_html)
        edge = AppProbe("Chromium", [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "--new-window", page])
        probes.append(edge)
        if edge.hwnd:
            priced["Chromium"] = price_target(
                "B. Chromium (Edge, a page input focused)", edge.hwnd)
        else:
            note("Edge did not give a window; skipped.")

        notepad = AppProbe("WinUI", ["notepad.exe"])
        probes.append(notepad)
        if notepad.hwnd:
            priced["WinUI/XAML"] = price_target(
                "C. WinUI/XAML (Notepad, a blank document)", notepad.hwnd)
        else:
            note("Notepad did not give a window; skipped.")

        slow = Probe("KEYHAC-PROBE-COST-SLOW", 480, slow_ms=50)
        probes.append(slow)
        # Fewer samples: every call here waits behind a 50 ms sleep, and the
        # finding is the order of magnitude, not the third decimal.
        priced["slow pump (50 ms)"] = price_target(
            "D. a window that pumps slowly - 50 ms per message", slow.hwnd,
            samples=40)

        # -- the cases that decide it --------------------------------------
        section("E. a foreground application that never pumps (a hung app)")
        # Behind a watchdog, because the answer may be that there is no
        # answer: doc/dev/testing.md records every UIA call into a
        # non-pumping window blocking for over 20 s.  If GetFocusedElement
        # does the same, a hung foreground application stops the hook.
        hung = Probe("KEYHAC-PROBE-COST-HUNG", 900, self_activate=True, hang=True)
        probes.append(hung)
        time.sleep(0.5)
        note(f"foreground: {describe_hwnd(int(user32.GetForegroundWindow() or 0))}")
        note(f"Win32 focus: {describe_hwnd(keyboard_focus())}")
        note("cheap tier, against the hung window in front:")
        report_timing("cheap tier today (2 HWNDs + title)",
                      timed(win32_probe), SAMPLES)
        answer = {}

        def ask_the_hung_one():
            # Its own apartment: this thread may be parked here forever, and
            # the automation pointer is only being read.
            ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            started = time.perf_counter()
            element = UIElement.from_focus()
            answer["from_focus_ms"] = (time.perf_counter() - started) * 1000
            answer["element"] = describe_element(element)

        worker = threading.Thread(target=ask_the_hung_one, daemon=True)
        worker.start()
        worker.join(HUNG_WATCHDOG)
        if worker.is_alive():
            note(f"GetFocusedElement never came back: still blocked after "
                 f"{HUNG_WATCHDOG:g}s")
            check("E: the hung foreground app blocks GetFocusedElement", True,
                  "a per-keystroke read here would stop the hook")
        else:
            for key, value in answer.items():
                note(f"{key}: {value}")
            check("E: GetFocusedElement answered about a hung foreground app",
                  True, f"{answer.get('from_focus_ms', 0):.3f} ms")
        hung.close()

        section("F. an element whose process has exited")
        # macOS survives this locally: CFEqual on a dead AXUIElement is a
        # pointer comparison, no round trip.  A Windows probe holding the
        # previous focused element has to ask UIA, and the question is
        # whether that call returns at all.
        doomed = Probe("KEYHAC-PROBE-COST-DOOMED", 40)
        activate(doomed.hwnd)
        stale = UIElement.from_focus()
        note(f"before exit: {describe_element(stale)}")
        doomed.close()
        doomed.process.wait(5)
        time.sleep(0.5)
        activate(classic.hwnd)
        live = UIElement.from_focus()

        dead = {}

        def compare_with_the_dead():
            ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            started = time.perf_counter()
            dead["same"] = _same_element(stale._ptr, live._ptr)
            dead["compare_ms"] = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            dead["runtime_id"] = runtime_id(stale)
            dead["runtime_id_ms"] = (time.perf_counter() - started) * 1000

        worker = threading.Thread(target=compare_with_the_dead, daemon=True)
        worker.start()
        worker.join(HUNG_WATCHDOG)
        if worker.is_alive():
            note(f"still blocked after {HUNG_WATCHDOG:g}s")
            check("F: comparing against a dead element returns", False,
                  "an exited application would stall the probe")
        else:
            for key, value in dead.items():
                note(f"{key}: {value}")
            check("F: comparing against a dead element returns", True,
                  f"same={dead.get('same')} in "
                  f"{dead.get('compare_ms', 0):.3f} ms")

        # -- the verdict, in one table -------------------------------------
        section("summary - warm median per key event")
        note(f"{'target':<24}{'cheap tier':>12}{'read':>12}"
             f"{'+compare':>12}{'vs today':>12}")
        for label, costs in priced.items():
            if not costs:
                continue
            note(f"{label:<24}{costs['win32']:>10.3f}ms{costs['raw']:>10.3f}ms"
                 f"{costs['candidate']:>10.3f}ms"
                 f"{costs['candidate'] / max(costs['win32'], 1e-6):>10,.0f}x")
        note(f"macOS, for comparison: one AXFocusedUIElement read at "
             f"{MACOS_FOCUSED_READ_MS} ms + a local CFEqual")
    finally:
        for probe in probes:
            probe.close()
        if page and os.path.exists(page):
            try:
                os.unlink(page)
            except OSError:
                pass
    code = report()
    # os._exit, because a section above may leave a thread parked inside a COM
    # call that will not be interrupted and that ordinary shutdown waits on.
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
