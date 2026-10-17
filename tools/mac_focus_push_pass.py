"""Can focus changes be *pushed* to the key path on macOS? (issue #150)

    make mac-focus-push-pass
    .venv/bin/python tools/mac_focus_push_pass.py [--rounds N] [--watch SECONDS]

THE QUESTION.  `MacFocusProvider.get_focus` answers "has anything the key
tables read changed since the last key event?" by asking: four cross-process
AX reads inside the event-tap callback, every one served by the front
application's main thread.  The alternative is to be told - an `AXObserver`
on the front application, whose callback sets a dirty flag the key path reads
for free.  Issue #150 lists what has to be measured before that is a design,
and this pass measures the macOS half of it:

  A. Which notifications cover which probe component.  The cheap probe is
     (pid, focused element, window title); a push design needs one event for
     each, registered on the *application* element, and must know which of
     them actually fire.

  B. What registration costs, and who it waits for.  Observers are per pid, so
     an application switch re-registers - a stall moved from the key path to
     the switch path, not removed.  Priced against every running regular
     application (read-only: an observer reads nothing and changes nothing),
     and against a throwaway application that has stopped pumping.

  C. Ordering - the one that could make push *incorrect*.  The tap callback
     and the observer callback are both serviced by our main run loop.  If the
     application has already moved its focus when the next key reaches the
     tap, but the notification saying so has not yet been delivered, the key
     is dispatched against the previous focus.  This pass posts Tab then F13
     into its own throwaway application at a range of gaps and classifies
     every F13 the tap sees against ground truth (a poll taken in the same
     callback).  It also tries the fix the issue proposes - draining the
     observer's run loop source, alone, in a private run loop mode at the top
     of the tap callback - and reports both.

  D. (--watch) Real applications.  Follows the front application, observer
     re-registered on every switch, and polls the cheap probe alongside.  A
     probe change no notification explains within `MISS_WINDOW` is an
     application that raises nothing - the fallback case.  Read-only;
     use the machine normally while it runs.

WHAT IT DRIVES.  Its own throwaway applications only, spawned as child
processes (win_focus_pass.py's rule).  Section C posts key events, and checks
before every one that its own child is in front - a key typed into somebody's
editor is not a measurement.  Keyhac itself, if running, sees those keys too;
Tab and F13 are left alone by every shipped config, but quit it for a clean
run.

Paste the output back verbatim.
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import threading
import time

if sys.platform != "darwin":
    sys.exit(f"{__file__} is a macOS pass; this is {sys.platform}.")

import objc                                                          # noqa: E402
import ApplicationServices as AS                                     # noqa: E402
import Quartz                                                        # noqa: E402
from AppKit import (                                                 # noqa: E402
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSWorkspace,
    NSWorkspaceDidActivateApplicationNotification,
)
from Foundation import NSDate, NSObject, NSRunLoop                   # noqa: E402

from keyhac.platform.mac.uielement import AX_MESSAGING_TIMEOUT       # noqa: E402

#: The notifications a push design would register on the application element.
#: Window title changes are the open question: kAXTitleChangedNotification is
#: documented on the element whose title changed, and whether registering it
#: on the *application* delivers its windows' is what section A answers.
NOTIFICATIONS = (
    AS.kAXFocusedUIElementChangedNotification,
    AS.kAXFocusedWindowChangedNotification,
    AS.kAXMainWindowChangedNotification,
    AS.kAXTitleChangedNotification,
)

#: A private run loop mode holding only the observer's source, so that a tap
#: callback can deliver pending AX notifications and nothing else.
DRAIN_MODE = "KeyhacFocusPushDrain"

#: Section C's gaps between the Tab that moves focus and the key typed after
#: it.  Human inter-key intervals start around 30 ms for a fast typist;
#: the low end is there to find the edge, not because a person types it.
GAPS_MS = (0, 2, 5, 10, 20, 50)

#: Section D: how long a probe change may go without a notification before it
#: counts as missed.  Generous - the question is "never", not "late".
MISS_WINDOW = 0.5

KEY_TAB = 48
#: The key that follows the Tab.  Not a printing key: once an `x` had been
#: typed into a field, every later Tab stopped moving the focus at all, and
#: section C reported 100% "early" at every gap - the harness, not the design.
#: F13 edits nothing and no shipped config binds it.
KEY_NEXT = 105


# -- the throwaway application ------------------------------------------------

#: Driven over stdin, one command per line, so every change is made alone and
#: stamped: `time.monotonic()` is mach_absolute_time on macOS, one clock for
#: every process, so the parent can subtract the child's stamps from its own.
#: `finishLaunching()` is not optional - without it the AX server never
#: registers (doc/dev/testing.md).
CHILD_SOURCE = r"""
import sys, threading, time
from AppKit import (NSAllRomanInputSourcesLocaleIdentifier, NSApplication,
                    NSApplicationActivationPolicyRegular,
                    NSBackingStoreBuffered, NSMakeRect, NSTextField, NSWindow)
from PyObjCTools import AppHelper

app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
app.finishLaunching()

def make_window(title, x):
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, 300, 340, 140), 1 << 0, NSBackingStoreBuffered, False)
    window.setReleasedWhenClosed_(False)
    window.setTitle_(title)
    fields = []
    for y in (80, 30):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, y, 300, 24))
        window.contentView().addSubview_(field)
        fields.append(field)
    fields[0].setNextKeyView_(fields[1])
    fields[1].setNextKeyView_(fields[0])
    if roman:
        # Keys typed here skip the input method server, which otherwise
        # delivers them to the field 35-100 ms late (measured, a Japanese IME
        # active): this window only, the user's input source is untouched.
        editor = window.fieldEditor_forObject_(True, None)
        editor.inputContext().setAllowedInputSourceLocales_(
            [NSAllRomanInputSourcesLocaleIdentifier])
    return window, fields

roman = sys.argv[2] == "1"
window, fields = make_window(sys.argv[1], 240)
window.makeKeyAndOrderFront_(None)
window.makeFirstResponder_(fields[0])
app.activateIgnoringOtherApps_(True)
second = None

def say(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()

def perform(line):
    global second
    verb, _, arg = line.partition(" ")
    if verb == "focus":
        window.makeFirstResponder_(fields[int(arg)])
    elif verb == "title":
        window.setTitle_(arg)
    elif verb == "open":
        second, _ = make_window("second window", 620)
        second.makeKeyAndOrderFront_(None)
    elif verb == "close":
        second.close()
        window.makeKeyAndOrderFront_(None)
    elif verb == "front":
        window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
    elif verb == "hang":
        time.sleep(float(arg))
    say("done %s %.6f" % (verb, time.monotonic()))

def read():
    for line in sys.stdin:
        AppHelper.callAfter(perform, line.strip())
    AppHelper.callAfter(app.terminate_, None)

threading.Thread(target=read, daemon=True).start()
say("ready")
# app.run(), not a spun NSRunLoop: running the run loop services its sources
# but dispatches no NSEvent, so a posted Tab would never reach the field.
app.run()
"""


class Child:

    def __init__(self, title, roman=False):
        self.process = subprocess.Popen(
            [sys.executable, "-c", CHILD_SOURCE, title, str(int(roman))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONWARNINGS": "ignore"})
        self.pid = self.process.pid
        self.lines = []
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()
        self.wait_for("ready", 15)

    def _reader(self):
        for line in self.process.stdout:
            with self.lock:
                self.lines.append(line.strip())

    def wait_for(self, prefix, timeout=5.0):
        """Pump our run loop until the child says *prefix*; its stamp."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for index, line in enumerate(self.lines):
                    if line.startswith(prefix):
                        del self.lines[:index + 1]
                        parts = line.split()
                        return float(parts[-1]) if len(parts) > 2 else 0.0
            pump(0.005)
        return None

    def send(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def do(self, command, timeout=5.0):
        self.send(command)
        return self.wait_for(f"done {command.split()[0]}", timeout)

    def close(self):
        try:
            self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.wait(3)
        except Exception:
            self.process.kill()


# -- helpers ------------------------------------------------------------------

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def note(text):
    print(f"       {text}", flush=True)


def section(title):
    print(f"\n=== {title} ===", flush=True)


def pump(seconds, mode=None):
    if mode is None:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(seconds))
    else:
        Quartz.CFRunLoopRunInMode(mode, seconds, False)


def ax_get(element, attribute):
    if element is None:
        return None
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    return value if err == 0 else None


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return bool(AS.CFEqual(a, b))
    except Exception:
        return False


def front_pid():
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(front.processIdentifier()) if front is not None else None


def probe(pid):
    """The cheap tier exactly as get_focus() reads it: (pid, element, title)."""
    app = AS.AXUIElementCreateApplication(pid)
    element = ax_get(app, "AXFocusedUIElement")
    window = ax_get(app, "AXFocusedWindow")
    title = ax_get(window, "AXTitle") if window is not None else None
    return (pid, element, str(title) if title is not None else None)


def same_probe(a, b):
    return (a is not None and b is not None and a[0] == b[0]
            and a[2] == b[2] and same(a[1], b[1]))


def ms(seconds):
    return f"{seconds * 1000:7.2f} ms"


def summarise(values):
    if not values:
        return "no samples"
    values = sorted(values)
    return (f"median {ms(statistics.median(values))}  "
            f"p95 {ms(values[max(int(len(values) * 0.95) - 1, 0)])}  "
            f"max {ms(values[-1])}  (n={len(values)})")


# -- the observer -------------------------------------------------------------

class Observer:
    """One AXObserver on one application element, recording every arrival."""

    def __init__(self, pid, modes=(Quartz.kCFRunLoopDefaultMode, DRAIN_MODE)):
        self.pid = pid
        self.arrivals = []          # (monotonic, notification, element)
        self.registered = {}        # notification -> AXError
        self.timings = {}

        @objc.callbackFor(AS.AXObserverCreate)
        def callback(observer, element, notification, refcon):
            self.arrivals.append((time.monotonic(), str(notification), element))

        self._callback = callback   # the closure must outlive the observer
        started = time.monotonic()
        err, self.observer = AS.AXObserverCreate(pid, callback, None)
        self.timings["create"] = time.monotonic() - started
        if err != 0:
            self.observer = None
            self.registered = {name: err for name in NOTIFICATIONS}
            return
        self.app = AS.AXUIElementCreateApplication(pid)
        started = time.monotonic()
        for name in NOTIFICATIONS:
            self.registered[name] = AS.AXObserverAddNotification(
                self.observer, self.app, name, None)
        self.timings["add"] = time.monotonic() - started
        self.source = AS.AXObserverGetRunLoopSource(self.observer)
        loop = Quartz.CFRunLoopGetCurrent()
        for mode in modes:
            Quartz.CFRunLoopAddSource(loop, self.source, mode)
        self.modes = modes

    def close(self):
        if self.observer is None:
            return
        loop = Quartz.CFRunLoopGetCurrent()
        for mode in self.modes:
            Quartz.CFRunLoopRemoveSource(loop, self.source, mode)
        for name, err in self.registered.items():
            if err == 0:
                AS.AXObserverRemoveNotification(self.observer, self.app, name)
        self.observer = None

    def since(self, index):
        return self.arrivals[index:]


# -- A: which notification covers which probe component -----------------------

def section_a(child):
    section("A. which notifications fire, per probe component")
    observer = Observer(child.pid)
    for name, err in observer.registered.items():
        note(f"register {name:<40} AXError {err}")
    check("A: every notification registers on the application element",
          all(err == 0 for err in observer.registered.values()))
    pump(0.5)

    steps = [
        ("focus 1", "focus moves to a second field, same window and title"),
        ("focus 0", "and back"),
        ("title renamed", "window title changes, focus does not move"),
        ("open", "a second window of the same application takes focus"),
        ("close", "it closes; focus returns to the first window"),
    ]
    covered = {}
    for command, meaning in steps:
        before = probe(child.pid)
        mark = len(observer.arrivals)
        stamp = child.do(command)
        pump(0.6)
        after = probe(child.pid)
        arrived = observer.since(mark)
        names = sorted({name for _, name, _ in arrived})
        latency = (min(t for t, _, _ in arrived) - stamp
                   if arrived and stamp else None)
        changed = not same_probe(before, after)
        note(f"{command:<14} {meaning}")
        note(f"{'':<14} probe changed: {changed}   notifications: "
             f"{', '.join(names) or 'NONE'}"
             + (f"   first after {ms(latency)}" if latency is not None else ""))
        covered[command] = (changed, bool(arrived))

    missed = [c for c, (changed, pushed) in covered.items() if changed and not pushed]
    check("A: every probe change was announced by some notification",
          not missed, ("unannounced: " + ", ".join(missed)) if missed else "")
    observer.close()


# -- B: registration cost -----------------------------------------------------

def section_b(child):
    section("B. registration cost - every running regular application")
    rows = []
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != NSApplicationActivationPolicyRegular:
            continue
        pid = int(app.processIdentifier())
        if pid == os.getpid():
            continue
        started = time.monotonic()
        observer = Observer(pid, modes=(DRAIN_MODE,))
        total = time.monotonic() - started
        observer.close()
        failed = {n: e for n, e in observer.registered.items() if e != 0}
        rows.append((total, str(app.localizedName()), pid, failed))
    for total, name, pid, failed in sorted(rows, reverse=True):
        extra = ("   failed: " + ", ".join(f"{n.replace('AX', '').replace('Notification', '')}={e}"
                                         for n, e in failed.items())) if failed else ""
        note(f"{name[:30]:<30} pid {pid:<6} {ms(total)}{extra}")
    note("all: " + summarise([total for total, *_ in rows]))

    section("B2. registration against an application that has stopped pumping")
    note(f"(messaging timeout, process-wide: {AX_MESSAGING_TIMEOUT * 1000:.0f} ms)")
    child.send("hang 3")
    pump(0.3)
    started = time.monotonic()
    observer = Observer(child.pid, modes=(DRAIN_MODE,))
    total = time.monotonic() - started
    for name, err in observer.registered.items():
        note(f"register {name:<40} AXError {err}")
    note(f"create {ms(observer.timings.get('create', 0))}   "
         f"add x{len(NOTIFICATIONS)} {ms(observer.timings.get('add', 0))}   "
         f"total {ms(total)}")
    check("B2: registering on a hung application is bounded by the messaging "
          "timeout", total < AX_MESSAGING_TIMEOUT * len(NOTIFICATIONS) + 0.1,
          ms(total))
    child.wait_for("done hang", 5)
    pump(0.3)
    # Did registration take, despite the hang?  Move focus and listen.
    mark = len(observer.arrivals)
    child.do("focus 1")
    pump(0.6)
    child.do("focus 0")
    pump(0.6)
    arrived = observer.since(mark)
    note(f"after it recovered: {len(arrived)} notifications "
         f"({', '.join(sorted({n for _, n, _ in arrived})) or 'none'})")
    observer.close()


# -- C: ordering ----------------------------------------------------------------

#: A run loop mode holding only the tap's source: section C's "busy" variant
#: runs it so that keys keep flowing while notifications pile up undelivered.
TAP_ONLY_MODE = "KeyhacFocusPushTapOnly"


class Tap:
    """A listen-only session tap classifying every F13 it sees."""

    def __init__(self, child, observer):
        self.child = child
        self.observer = observer
        self.reset()

        def callback(proxy, kind, event, refcon):
            if kind != Quartz.kCGEventKeyDown:
                return event
            code = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            if code == KEY_TAB and self.tab_at is None:
                self.tab_at = time.monotonic()
            elif code == KEY_NEXT:
                self.on_next()
            return event

        self._callback = callback
        self.port = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown), callback, None)
        if self.port is None:
            raise RuntimeError("CGEventTapCreate failed - Input Monitoring?")
        self.source = Quartz.CFMachPortCreateRunLoopSource(None, self.port, 0)
        for mode in (Quartz.kCFRunLoopDefaultMode, TAP_ONLY_MODE):
            Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), self.source,
                                      mode)

    def reset(self, before=None, mark=0):
        self.before = before        # the focused element before the Tab
        self.mark = mark            # arrivals index before the Tab
        self.tab_at = None          # when the Tab passed this tap
        self.seen = None            # (pushed, drained, moved, next_at)

    def on_next(self):
        if self.before is None or self.seen is not None:
            return
        next_at = time.monotonic()
        pushed = len(self.observer.arrivals) > self.mark
        # The proposed fix: deliver whatever is already queued for the
        # observer - and only that, since nothing else is in this mode.
        Quartz.CFRunLoopRunInMode(DRAIN_MODE, 0, True)
        drained = len(self.observer.arrivals) > self.mark
        # Ground truth, taken last so a notification racing the poll counts
        # against the design rather than for it.
        now = ax_get(AS.AXUIElementCreateApplication(self.child.pid),
                     "AXFocusedUIElement")
        moved = not same(now, self.before)
        self.seen = (pushed, drained, moved, next_at)

    def close(self):
        for mode in (Quartz.kCFRunLoopDefaultMode, TAP_ONLY_MODE):
            Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetCurrent(),
                                         self.source, mode)
        Quartz.CGEventTapEnable(self.port, False)


def post(code):
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, code, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def run_mode_until(mode, done, timeout):
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        Quartz.CFRunLoopRunInMode(mode, 0.001, True)


def section_c(child, rounds, label):
    section(f"C. ordering - is the notification in before the next key? ({label})")
    note("Tab moves focus between the child's two fields; F13 follows once "
         "the Tab has passed the tap and the gap has elapsed.")
    note("  ok     = notification delivered before the F13 callback")
    note("  stale  = focus had moved, nothing delivered: push dispatches the "
         "key against the previous focus")
    note("  early  = focus had not moved yet: a poll would be just as wrong, "
         "so not push's fault")
    note("idle: our run loop is free in the gap (Keyhac between keys).  busy: "
         "only the tap is serviced, so notifications wait for the drain.")
    observer = Observer(child.pid)
    tap = Tap(child, observer)
    child.do("front")
    pump(0.5)
    totals = {"stale": 0, "stale_after_drain": 0}
    latencies = []              # Tab at the tap -> first notification
    try:
        for variant, mode in (("idle", Quartz.kCFRunLoopDefaultMode),
                              ("busy", TAP_ONLY_MODE)):
            for gap in GAPS_MS:
                counts = {"ok": 0, "stale": 0, "drain_fixed": 0, "early": 0,
                          "lost": 0}
                for _ in range(rounds):
                    if front_pid() != child.pid:
                        child.do("front")
                        pump(0.3)
                        if front_pid() != child.pid:
                            check("C: the child is in front before posting",
                                  False, f"front pid {front_pid()}; stopped")
                            return
                    app = AS.AXUIElementCreateApplication(child.pid)
                    before = ax_get(app, "AXFocusedUIElement")
                    pump(0.05)
                    mark = len(observer.arrivals)
                    tap.reset(before, mark)
                    post(KEY_TAB)
                    # The gap is counted from the Tab passing *our* tap, which
                    # is when Keyhac would have released it to the application.
                    run_mode_until(mode, lambda: tap.tab_at is not None, 1.0)
                    if tap.tab_at is not None:
                        end = tap.tab_at + gap / 1000
                        run_mode_until(mode, lambda: time.monotonic() >= end, 1.0)
                    post(KEY_NEXT)
                    run_mode_until(mode, lambda: tap.seen is not None, 1.0)
                    if tap.seen is None:
                        counts["lost"] += 1
                        pump(0.1)
                        continue
                    pushed, drained, moved, _ = tap.seen
                    if not moved:
                        counts["early"] += 1
                    elif pushed:
                        counts["ok"] += 1
                    elif drained:
                        counts["drain_fixed"] += 1
                    else:
                        counts["stale"] += 1
                    pump(0.1)
                    if variant == "idle" and tap.tab_at is not None:
                        arrived = observer.since(mark)
                        if arrived:
                            latencies.append(arrived[0][0] - tap.tab_at)
                totals["stale"] += counts["stale"] + counts["drain_fixed"]
                totals["stale_after_drain"] += counts["stale"]
                note(f"{variant} gap {gap:>3} ms: ok {counts['ok']:>3}   "
                     f"stale {counts['stale'] + counts['drain_fixed']:>3} "
                     f"(drain fixes {counts['drain_fixed']:>3}, leaves "
                     f"{counts['stale']:>3})   early {counts['early']:>3}   "
                     f"lost {counts['lost']}")
    finally:
        tap.close()
        observer.close()
    note("Tab passing the tap -> first notification: " + summarise(latencies))
    check(f"C ({label}): with the drain, no key saw a moved focus before "
          f"its notification",
          totals["stale_after_drain"] == 0,
          f"{totals['stale']} stale without the drain, "
          f"{totals['stale_after_drain']} with it")


# -- D: real applications -------------------------------------------------------

class Activation(NSObject):

    def initWithHandler_(self, handler):
        self = objc.super(Activation, self).init()
        self.handler = handler
        return self

    def activated_(self, notification):
        self.handler()


def section_d(seconds):
    section(f"D. real applications for {seconds:g}s - use the machine normally")
    state = {"observer": None, "switches": [], "pid": None}

    def follow():
        pid = front_pid()
        if pid is None or pid == state["pid"]:
            return
        if state["observer"] is not None:
            state["observer"].close()
        started = time.monotonic()
        state["observer"] = Observer(pid) if pid != os.getpid() else None
        state["switches"].append((time.monotonic() - started, pid))
        state["pid"] = pid

    activation = Activation.alloc().initWithHandler_(follow)
    centre = NSWorkspace.sharedWorkspace().notificationCenter()
    centre.addObserver_selector_name_object_(
        activation, "activated:", NSWorkspaceDidActivateApplicationNotification,
        None)
    follow()

    previous = None
    pending = []                # (monotonic, pid, arrivals mark) - unexplained
    changes = missed = 0
    missed_by_app = {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(0.02)
        observer = state["observer"]
        if observer is None:
            previous = None
            continue
        current = probe(observer.pid)
        if previous is not None and current[0] == previous[0] \
                and not same_probe(current, previous):
            changes += 1
            pending.append((time.monotonic(), observer, len(observer.arrivals) - 1))
        previous = current
        still = []
        for started, obs, mark in pending:
            # An arrival in the window before the poll noticed counts too:
            # the notification may well beat a 20 ms poll.
            explained = any(t >= started - 0.1 for t, _, _ in obs.arrivals[max(mark - 5, 0):])
            if explained:
                continue
            if time.monotonic() - started > MISS_WINDOW:
                missed += 1
                name = NSWorkspace.sharedWorkspace().frontmostApplication()
                key = str(name.localizedName()) if name else str(obs.pid)
                missed_by_app[key] = missed_by_app.get(key, 0) + 1
            else:
                still.append((started, obs, mark))
        pending = still

    centre.removeObserver_(activation)
    if state["observer"] is not None:
        state["observer"].close()
    note(f"application switches: {len(state['switches'])}   re-registration "
         + summarise([t for t, _ in state["switches"]]))
    note(f"probe changes within one application: {changes}   "
         f"unannounced after {MISS_WINDOW:g}s: {missed}")
    for name, count in sorted(missed_by_app.items(), key=lambda kv: -kv[1]):
        note(f"  {name}: {count}")


# -- main -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rounds", type=int, default=30,
                        help="section C rounds per gap (default 30)")
    parser.add_argument("--watch", type=float, default=0,
                        help="run section D for this many seconds")
    parser.add_argument("--only-watch", action="store_true",
                        help="skip A-C (they drive a throwaway window)")
    args = parser.parse_args()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    # Process-wide, exactly as MacFocusProvider sets it (issue #175).
    AS.AXUIElementSetMessagingTimeout(AS.AXUIElementCreateSystemWide(),
                                      AX_MESSAGING_TIMEOUT)
    print(__doc__.split("\n\n")[0])
    if not AS.AXIsProcessTrusted():
        sys.exit("This process is not trusted for Accessibility.")

    if not args.only_watch:
        child = Child("keyhac push pass", roman=True)
        try:
            pump(0.5)
            if front_pid() != child.pid:
                child.do("front")
                pump(0.5)
            note(f"child pid {child.pid}, front pid {front_pid()}")
            section_a(child)
            section_b(child)
            section_c(child, args.rounds, "Roman input source")
        finally:
            child.close()
        # Again with whatever input source the operator has selected: an
        # input method delays every key on its way to the application, which
        # is how the user actually types, and is reported separately because
        # it changes how much of the result is "early".
        child = Child("keyhac push pass (IME)")
        try:
            pump(0.5)
            section_c(child, args.rounds, "operator's input source")
        finally:
            child.close()
    if args.watch:
        section_d(args.watch)

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
