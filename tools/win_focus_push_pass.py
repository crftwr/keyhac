"""Can focus changes be *pushed* to the key path on Windows? (issue #150)

    python tools/win_focus_push_pass.py [--rounds N]

THE QUESTION.  `SetWinEventHook` is the cheap way to be told about focus:
out-of-context events are posted to the registering thread's queue, which is
the same main-thread pump the keyboard hook is delivered on, so the callback
needs no COM, no apartment and no marshalling.  But it is an **MSAA** event,
and the tree `focus_path_pattern=` is built from is **UIA**.  A provider that
implements UIA natively is under no obligation to raise the legacy event as
well - and if WinUI/XAML or WPF do not, the cheap mechanism is blind exactly
where #145's gap is widest.  That is the first thing issue #150 says to
measure, before any design:

  A. Which layer fires.  A system-wide WinEvent hook counts
     EVENT_OBJECT_FOCUS arrivals per focus move, against three providers:
     a classic Win32 dialog (USER32 raises the event itself), Chromium
     (Edge, a page with two inputs), and WinUI/XAML (Notepad).  Chromium is
     driven twice - with Tab *before* anything here has asked it a UIA
     question, and again after - because Chromium raises its MSAA/IA2 events
     only once accessibility is on, and a UIA client is what turns it on.
     Which of the two is the real-world answer depends on whether something
     else on the machine already did.

  B. Ordering.  The hook callback and a queued WinEvent are both delivered
     while the main thread pumps.  Tab then F13 at a range of gaps, with a
     WH_KEYBOARD_LL hook on the same thread classifying each F13 against
     ground truth (GUITHREADINFO for the classic window - local, it asks
     nobody), and trying the proposed fix: PeekMessage(PM_NOREMOVE) at the top
     of the hook callback, which delivers queued out-of-context events.

  C. Registration.  One global hook, registered once - priced, and timed
     again with a hung application in front, where it must not care.

WHAT IT DRIVES.  Its own throwaway windows and its own disposable Edge and
Notepad windows (win_probe_cost_pass.py's).  Keys are injected only after
checking that one of those is the foreground window.  Quit Keyhac for a clean
run: it would see the injected Tab and F13 as well.

Paste the output back verbatim.
"""

import sys

if sys.platform != "win32":
    sys.exit(f"{__file__} is a Windows pass; this is {sys.platform}.")

import argparse                                                      # noqa: E402
import ctypes                                                        # noqa: E402
import os                                                            # noqa: E402
import statistics                                                    # noqa: E402
import subprocess                                                    # noqa: E402
import tempfile                                                      # noqa: E402
import time                                                          # noqa: E402
from ctypes import wintypes                                          # noqa: E402

import win_probe_cost_pass as cost                                   # noqa: E402
from win_probe_cost_pass import (                                    # noqa: E402
    AppProbe, activate, check, describe_element, describe_hwnd,
    keyboard_focus, note, report, section, user32, kernel32)
from keyhac.platform.win.uielement import (                          # noqa: E402
    UIElement, _same_element)

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_FOCUS = 0x8005
EVENT_OBJECT_NAMECHANGE = 0x800C
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_CLIENT = -4

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
PM_NOREMOVE = 0x0000
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
KEYEVENTF_KEYUP = 0x0002
VK_TAB = 0x09
#: The key after the Tab.  Not a printing key: on macOS a typed `x` stopped
#: later Tabs moving the focus and made the whole ordering section read as
#: "early" (tools/mac_focus_push_pass.py).  F13 edits nothing.
VK_F13 = 0x7C

GAPS_MS = (0, 2, 5, 10, 20, 50)

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
    wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM,
                              wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


# Every prototype explicit (CLAUDE.md): a c_int restype truncates handles.
user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                     wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM,
                                  wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.MsgWaitForMultipleObjects.argtypes = [
    wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
    wintypes.DWORD]
user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def pid_of(hwnd):
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return owner.value


def pump(seconds):
    """Pump this thread's queue - which is what delivers out-of-context
    WinEvents and low-level hook calls alike - for *seconds*."""
    msg = wintypes.MSG()
    end = time.perf_counter() + seconds
    while True:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        left = end - time.perf_counter()
        if left <= 0:
            return
        user32.MsgWaitForMultipleObjects(0, None, False,
                                         max(1, int(left * 1000)), QS_ALLINPUT)


def pump_until(done, timeout):
    end = time.perf_counter() + timeout
    while not done() and time.perf_counter() < end:
        pump(0.001)


def tap(vk):
    user32.keybd_event(vk, 0, 0, None)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)


def ms(seconds):
    return f"{seconds * 1000:7.2f} ms"


def summarise(values):
    if not values:
        return "no samples"
    values = sorted(values)
    return (f"median {ms(statistics.median(values))}  "
            f"p95 {ms(values[max(int(len(values) * 0.95) - 1, 0)])}  "
            f"max {ms(values[-1])}  (n={len(values)})")


# -- the event source being measured -----------------------------------------

class WinEvents:
    """System-wide out-of-context hooks for the three events a push design
    would need: focus, foreground and name (the window title)."""

    def __init__(self):
        self.arrivals = []      # (perf_counter, event, hwnd, idObject, idChild)

        def callback(hook, event, hwnd, id_object, id_child, thread, when):
            self.arrivals.append((time.perf_counter(), event, int(hwnd or 0),
                                  id_object, id_child))

        self._callback = WINEVENTPROC(callback)   # must outlive the hooks
        started = time.perf_counter()
        self.hooks = [
            user32.SetWinEventHook(event, event, None, self._callback, 0, 0,
                                   WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            for event in (EVENT_OBJECT_FOCUS, EVENT_SYSTEM_FOREGROUND,
                          EVENT_OBJECT_NAMECHANGE)]
        self.register_time = time.perf_counter() - started

    def since(self, mark, event=None, pid=None):
        found = self.arrivals[mark:]
        if event is not None:
            found = [a for a in found if a[1] == event]
        if pid is not None:
            found = [a for a in found if a[2] and pid_of(a[2]) == pid]
        return found

    def close(self):
        for hook in self.hooks:
            if hook:
                user32.UnhookWinEvent(hook)
        self.hooks = []


# -- the classic window ---------------------------------------------------------

#: Two EDITs that Tab moves between: IsDialogMessage in the loop is what gives
#: a plain window dialog navigation, and USER32 raises EVENT_OBJECT_FOCUS on
#: every SetFocus by itself - the control group of section A.
CLASSIC_SOURCE = r"""
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
user32.SetFocus.argtypes = [wintypes.HWND]
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.IsDialogMessageW.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.MSG)]
user32.IsDialogMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

def wndproc(h, m, w, l):
    return user32.DefWindowProcW(h, m, w, l)

proc = WNDPROC(wndproc)
wc = WNDCLASSW()
wc.lpfnWndProc = proc
wc.hInstance = kernel32.GetModuleHandleW(None)
wc.lpszClassName = "KeyhacFocusPush"
user32.RegisterClassW(ctypes.byref(wc))
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_BORDER, WS_TABSTOP = 0x40000000, 0x10000000, 0x00800000, 0x00010000
WS_EX_CONTROLPARENT = 0x00010000
hwnd = user32.CreateWindowExW(WS_EX_CONTROLPARENT, "KeyhacFocusPush", sys.argv[1],
                              WS_OVERLAPPEDWINDOW, 40, 80, 420, 160, None, None,
                              wc.hInstance, None)
edits = [user32.CreateWindowExW(
    0, "EDIT", name, WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP,
    20, y, 200, 26, hwnd, None, wc.hInstance, None)
    for name, y in (("first", 20), ("second", 60))]
user32.ShowWindow(hwnd, 5)
user32.SetFocus(edits[0])
print(" ".join(str(int(h)) for h in (hwnd, *edits)), flush=True)
msg = wintypes.MSG()
while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    if not user32.IsDialogMessageW(hwnd, ctypes.byref(msg)):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
"""


class Classic:

    def __init__(self, title):
        self.process = subprocess.Popen(
            [sys.executable, "-c", CLASSIC_SOURCE, title],
            stdout=subprocess.PIPE, text=True)
        self.hwnd, *self.edits = (int(h) for h in
                                  self.process.stdout.readline().split())
        self.pid = self.process.pid
        time.sleep(0.3)

    def close(self):
        self.process.terminate()


# -- A: which layer fires -------------------------------------------------------

def tab_moves(events, label, hwnd, rounds, uia_truth):
    """Inject Tab *rounds* times into *hwnd*; count the focus events its
    process raised for each.  With *uia_truth*, also confirm through UIA that
    the focus really moved - which is itself a UIA client, so it is left out
    of the first Chromium run on purpose."""
    pid = pid_of(hwnd)
    if not activate(hwnd):
        check(f"A {label}: window is foreground", False,
              describe_hwnd(int(user32.GetForegroundWindow() or 0)))
        return
    pump(0.5)
    counts, moved, latencies, shapes = [], 0, [], {}
    for _ in range(rounds):
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            check(f"A {label}: stayed in front", False, "stopped injecting")
            break
        before = UIElement.from_focus() if uia_truth else None
        mark = len(events.arrivals)
        started = time.perf_counter()
        tap(VK_TAB)
        pump(0.4)
        arrived = events.since(mark, EVENT_OBJECT_FOCUS, pid)
        counts.append(len(arrived))
        for _, _, _, id_object, id_child in arrived:
            key = (id_object, id_child if id_child <= 0 else ">0")
            shapes[key] = shapes.get(key, 0) + 1
        if arrived:
            latencies.append(arrived[0][0] - started)
        if uia_truth:
            after = UIElement.from_focus()
            if before is not None and after is not None \
                    and _same_element(before._ptr, after._ptr) is False:
                moved += 1
    fired = sum(1 for c in counts if c)
    note(f"{label}: {fired}/{len(counts)} Tabs raised EVENT_OBJECT_FOCUS"
         + (f"   (UIA saw the focus move {moved}/{len(counts)})"
            if uia_truth else "   (UIA not consulted)"))
    note(f"{'':<4}events per Tab: {counts}")
    note(f"{'':<4}(idObject, idChild): "
         + ", ".join(f"{k}x{v}" for k, v in sorted(shapes.items())))
    note(f"{'':<4}Tab injected -> first event: {summarise(latencies)}")
    return fired, len(counts), moved


def uia_moves(events, label, hwnd, rounds):
    """Move the focus between two focusable elements through UIA SetFocus -
    for a provider where Tab does not move focus (Notepad's document takes
    the Tab as a character)."""
    pid = pid_of(hwnd)
    if not activate(hwnd):
        check(f"A {label}: window is foreground", False)
        return
    root = UIElement.from_hwnd(hwnd)
    candidates, queue = [], [(root, 0)]
    while queue and len(candidates) < 2:
        element, depth = queue.pop(0)
        if element is None or depth > 10:
            continue
        role = element.get_attribute_value("ControlType")
        if role in ("Edit", "Document", "Button", "TabItem") \
                and not element.is_offscreen():
            element.set_focus()
            pump(0.2)
            if element.has_focus():
                candidates.append(element)
        queue.extend((child, depth + 1) for child in element.children())
    if len(candidates) < 2:
        note(f"{label}: found {len(candidates)} element(s) that take focus; "
             f"skipped")
        return
    for element in candidates:
        note(f"{label}: target {describe_element(element)}")
    counts, moved, latencies = [], 0, []
    for index in range(rounds):
        target = candidates[index % 2]
        mark = len(events.arrivals)
        started = time.perf_counter()
        target.set_focus()
        pump(0.4)
        arrived = events.since(mark, EVENT_OBJECT_FOCUS, pid)
        counts.append(len(arrived))
        if arrived:
            latencies.append(arrived[0][0] - started)
        if target.has_focus():
            moved += 1
    fired = sum(1 for c in counts if c)
    note(f"{label}: {fired}/{len(counts)} SetFocus moves raised "
         f"EVENT_OBJECT_FOCUS   (focus landed {moved}/{len(counts)})")
    note(f"{'':<4}events per move: {counts}")
    note(f"{'':<4}SetFocus -> first event: {summarise(latencies)}")
    return fired, len(counts), moved


def title_change(events, label, hwnd):
    """Rename the window; is EVENT_OBJECT_NAMECHANGE raised for it?"""
    pid = pid_of(hwnd)
    user32.SetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    mark = len(events.arrivals)
    user32.SetWindowTextW(hwnd, "keyhac push pass - renamed")
    pump(0.4)
    arrived = [a for a in events.since(mark, EVENT_OBJECT_NAMECHANGE, pid)
               if a[2] == hwnd]
    note(f"{label}: window rename raised {len(arrived)} "
         f"EVENT_OBJECT_NAMECHANGE on the top-level HWND")
    return bool(arrived)


# -- B: ordering ------------------------------------------------------------------

class KeyHook:
    """WH_KEYBOARD_LL on this thread, classifying every F13 it sees."""

    def __init__(self, events, truth):
        self.events = events
        self.truth = truth          # () -> hashable focus, read locally
        self.reset()

        def callback(code, wparam, lparam):
            if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT))[0]
                if info.vkCode == VK_TAB and self.tab_at is None:
                    self.tab_at = time.perf_counter()
                elif info.vkCode == VK_F13 and self.seen is None \
                        and self.before is not None:
                    self.on_next()
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._callback = HOOKPROC(callback)
        self.hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._callback, kernel32.GetModuleHandleW(None), 0)
        if not self.hook:
            raise OSError(ctypes.get_last_error(), "SetWindowsHookExW")

    def reset(self, before=None, mark=0):
        self.before, self.mark = before, mark
        self.tab_at = None
        self.seen = None

    def on_next(self):
        pushed = len(self.events.since(self.mark, EVENT_OBJECT_FOCUS)) > 0
        # The proposed fix.  PM_NOREMOVE retrieves nothing, but a Peek is a
        # point at which queued out-of-context events are delivered.
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        drained = len(self.events.since(self.mark, EVENT_OBJECT_FOCUS)) > 0
        moved = self.truth() != self.before
        self.seen = (pushed, drained, moved)

    def close(self):
        user32.UnhookWindowsHookEx(self.hook)


def section_b(events, classic, rounds):
    section("B. ordering - is the event in before the next key? "
            "(classic window, GUITHREADINFO as ground truth)")
    note("  ok    = focus event delivered before the F13 hook call")
    note("  stale = focus had moved, no event delivered: push dispatches the "
         "key against the previous focus")
    note("  early = focus had not moved yet: a poll would be as wrong")
    if not activate(classic.hwnd):
        check("B: classic window is foreground", False)
        return
    hook = KeyHook(events, keyboard_focus)
    totals = {"stale": 0, "after_drain": 0}
    try:
        for gap in GAPS_MS:
            counts = {"ok": 0, "stale": 0, "drain_fixed": 0, "early": 0,
                      "lost": 0}
            for _ in range(rounds):
                if int(user32.GetForegroundWindow() or 0) != classic.hwnd:
                    check("B: classic window stayed in front", False, "stopped")
                    return
                pump(0.05)
                hook.reset(keyboard_focus(), len(events.arrivals))
                user32.keybd_event(VK_TAB, 0, 0, None)
                pump_until(lambda: hook.tab_at is not None, 1.0)
                user32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, None)
                if hook.tab_at is not None:
                    end = hook.tab_at + gap / 1000
                    pump_until(lambda: time.perf_counter() >= end, 1.0)
                tap(VK_F13)
                pump_until(lambda: hook.seen is not None, 1.0)
                if hook.seen is None:
                    counts["lost"] += 1
                    continue
                pushed, drained, moved = hook.seen
                if not moved:
                    counts["early"] += 1
                elif pushed:
                    counts["ok"] += 1
                elif drained:
                    counts["drain_fixed"] += 1
                else:
                    counts["stale"] += 1
                pump(0.08)
            totals["stale"] += counts["stale"] + counts["drain_fixed"]
            totals["after_drain"] += counts["stale"]
            note(f"gap {gap:>3} ms: ok {counts['ok']:>3}   stale "
                 f"{counts['stale'] + counts['drain_fixed']:>3} (drain fixes "
                 f"{counts['drain_fixed']:>3}, leaves {counts['stale']:>3})   "
                 f"early {counts['early']:>3}   lost {counts['lost']}")
    finally:
        hook.close()
    check("B: with the drain, no key saw a moved focus before its event",
          totals["after_drain"] == 0,
          f"{totals['stale']} stale without the drain, "
          f"{totals['after_drain']} with it")


# -- main ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    print(__doc__.split("\n\n")[0])

    events = WinEvents()
    probes = []
    page = None
    try:
        section("C. registration")
        note(f"three system-wide out-of-context hooks: "
             f"{ms(events.register_time)}")
        check("C: SetWinEventHook registered all three",
              all(events.hooks), str(events.hooks))

        section("A1. classic Win32 (two EDITs, dialog navigation)")
        classic = Classic("KEYHAC-FOCUS-PUSH-CLASSIC")
        probes.append(classic)
        fired, total, _ = tab_moves(events, "classic", classic.hwnd,
                                    args.rounds, uia_truth=True) or (0, 1, 0)
        check("A1: USER32 raises EVENT_OBJECT_FOCUS for every Tab",
              fired == total, f"{fired}/{total}")
        title_change(events, "classic", classic.hwnd)

        page_html = ("<!doctype html><title>keyhac focus push</title><body>"
                     "<input autofocus size=40 placeholder=first><br>"
                     "<input size=40 placeholder=second></body>")
        handle, page = tempfile.mkstemp(suffix=".html", prefix="keyhac_push_")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(page_html)
        section("A2. Chromium (Edge, Tab between two page inputs)")
        edge = AppProbe("Chromium", [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "--new-window", page])
        probes.append(edge)
        if edge.hwnd:
            note("first pass: nothing here has asked Edge a UIA question")
            cold = tab_moves(events, "Chromium, cold", edge.hwnd, args.rounds,
                             uia_truth=False)
            note("second pass: UIA consulted on every Tab (accessibility on)")
            warm = tab_moves(events, "Chromium, warm", edge.hwnd, args.rounds,
                             uia_truth=True)
            if warm:
                check("A2: Chromium raises EVENT_OBJECT_FOCUS once UIA is "
                      "on", warm[0] == warm[1], f"{warm[0]}/{warm[1]}")
            if cold:
                note(f"before UIA: {cold[0]}/{cold[1]} - zero here means the "
                     f"WinEvent design needs Chromium's accessibility on, "
                     f"which only a UIA client turns on")
        else:
            note("Edge did not give a window; skipped.")

        section("A3. WinUI/XAML (Notepad, UIA SetFocus between two elements)")
        notepad = AppProbe("WinUI", ["notepad.exe"])
        probes.append(notepad)
        if notepad.hwnd:
            result = uia_moves(events, "WinUI", notepad.hwnd, args.rounds)
            if result:
                check("A3: WinUI raises the legacy EVENT_OBJECT_FOCUS",
                      result[0] == result[1], f"{result[0]}/{result[1]}  "
                      f"- the question #150 turns on")
        else:
            note("Notepad did not give a window; skipped.")

        section_b(events, classic, args.rounds)

        section("C2. a hung application in front")
        hung = cost.Probe("KEYHAC-FOCUS-PUSH-HUNG", 900, self_activate=True,
                          hang=True)
        probes.append(hung)
        time.sleep(0.5)
        note(f"foreground: "
             f"{describe_hwnd(int(user32.GetForegroundWindow() or 0))}")
        started = time.perf_counter()
        second = WinEvents()
        elapsed = time.perf_counter() - started
        second.close()
        started = time.perf_counter()
        pump(0.2)
        pumped = time.perf_counter() - started
        check("C2: registration does not wait for the hung application",
              elapsed < 0.05, ms(elapsed))
        check("C2: our pump keeps turning with it in front", pumped < 0.5,
              ms(pumped))
    finally:
        events.close()
        for probe in probes:
            probe.close()
        if page and os.path.exists(page):
            try:
                os.unlink(page)
            except OSError:
                pass
    code = report()
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
