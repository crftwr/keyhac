"""Live measurement of which application macOS says is in front (issue #45).

    make mac-focus-pass          (samples for a minute; switch apps while it runs)
    .venv/bin/python tools/mac_focus_pass.py [--seconds N] [--interval S]

THE QUESTION.  `MacFocusProvider.get_focus` builds one `Focus` out of two
independent sources and cross-checks neither:

    app = NSWorkspace.sharedWorkspace().frontmostApplication()   # app_name, pid
    focused_app = _ax_get(self._system_wide, "AXFocusedApplication")  # element

So `focus.app_name` can name a different application than `focus.element`
belongs to.  That is not only a reporting problem: `app_name` is what
`define_keytable(app=...)` matches on, so a divergence silently selects the
wrong key table.  `get_active_window()` (`keyhac/platform/mac/window.py`) asks
only the first source, and was once seen naming VS Code while the operator was
in another application.

Inference is not measurement, hence this pass.  It prints both answers side by
side, every time either moves, and counts the samples where they disagree.

**THE PRECONDITION THAT MAKES THIS MEASURABLE AT ALL.**  The system-wide
element answers `kAXErrorCannotComplete` (-25204) for `AXFocusedApplication`
in a process that has never created an `NSApplication` - which is every bare
script, this tool included, and pytest.  Calling
`NSApplication.sharedApplication()` flips the same read to an instant success;
nothing else about the process changes.  So this tool creates one (as an
accessory, so it never takes the focus it is measuring).  Without it the pass
would measure `get_focus`'s *fallback* - which derives the app element from
`frontmostApplication()`'s own pid - and would report perfect agreement
between the two sources by construction, which is exactly the wrong answer.

Keyhac runs a real `NSApplication`, so the primary read is the one it takes.

**THE SECOND PRECONDITION.**  `NSWorkspace`'s idea of the frontmost
application is refreshed by run-loop callbacks and by nothing else
(doc/dev/testing.md), so this pass pumps the run loop between samples instead
of sleeping.  A sleeping sampler reads the process-start snapshot forever, and
would report every application switch as a divergence - a bug this pass had
for one run before its own rule caught it.

WHAT IT READS, per sample:

    NSWorkspace  frontmostApplication()      - what app_name and window.py use
    AX           AXFocusedApplication        - what focus.element comes from
    menu bar     menuBarOwningApplication()  - the third answer AppKit offers,
                                               reported to show whether it
                                               agrees with either
    element      the focused element's role, from whichever app AX named

and, whenever that line changes, which applications claim `AXFrontmost` -
more than one claiming it at once is the second observation in issue #45.

WHAT IT TOUCHES.  By default nothing.  Every call is a read; no window is
raised, no application activated, no key posted.  Run it and use the machine
normally - the finding is in the transitions, so switch applications, click
into fields, open and close windows, and let a full-screen space or two go by.

`--drive` adds the switches, so that a run is conclusive with nobody at the
keyboard: it spawns a throwaway application of its own, which takes the focus
for a couple of seconds and exits, handing the focus back.  Its own
application and never one of the operator's - win_focus_pass.py's rule on the
other platform - but it does take the focus while it runs, which is a thing to
know before starting it under someone's hands.  It does not replace the
interactive run: a throwaway window switch is the simple path, and Spaces,
full-screen applications and Mission Control are not.

Paste the output back verbatim.  A divergence shows up here as two names on
one line, not as an error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

if sys.platform != "darwin":
    sys.exit(f"{__file__} is a macOS pass; this is {sys.platform}.")

import ApplicationServices as AS                                     # noqa: E402
from AppKit import (                                                 # noqa: E402
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSRunningApplication,
    NSWorkspace,
)
from Foundation import NSDate, NSRunLoop                             # noqa: E402

# What MacFocusProvider uses, so the pass waits exactly as long as key
# dispatch would.
AX_MESSAGING_TIMEOUT = 0.1

#: How long a driven throwaway application stays in front, and how long the
#: pass waits afterwards before driving the next one.  Both are generous: the
#: question is whether the two answers *converge*, so the interesting samples
#: are the ones just after a switch, and a switch that has already settled
#: costs nothing to keep watching.
DRIVE_LIFETIME = 2.5
DRIVE_GAP = 2.0

#: A throwaway application, spawned as a child process so that --drive can
#: produce real application switches without touching anything the operator
#: has open - win_focus_pass.py's rule on the other platform.  It is a
#: *regular* application with a key window and a focused text field, because
#: an accessory one never becomes frontmost and so switches nothing.
#: `finishLaunching()` is not optional: without it the child's AX server never
#: registers and every query into it fails with kAXErrorCannotComplete
#: (doc/dev/testing.md).
CHILD_SOURCE = """
import sys
from AppKit import (NSApplication, NSApplicationActivationPolicyRegular,
                    NSBackingStoreBuffered, NSMakeRect, NSTextField, NSWindow)
from Foundation import NSDate, NSRunLoop

app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
app.finishLaunching()

window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    NSMakeRect(240, 240, 340, 120), 1 << 0, NSBackingStoreBuffered, False)
window.setTitle_("keyhac mac_focus_pass")
field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 40, 300, 24))
window.contentView().addSubview_(field)
window.makeKeyAndOrderFront_(None)
window.makeFirstResponder_(field)
app.activateIgnoringOtherApps_(True)

NSRunLoop.currentRunLoop().runUntilDate_(
    NSDate.dateWithTimeIntervalSinceNow_(float(sys.argv[1])))
"""



def _ax_get(element, attribute):
    """One attribute and its error code - the provider's `_ax_get`, except
    that the code is what this pass is here to report."""
    if element is None:
        return -25204, None
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return -25204, None
    return err, (value if err == 0 else None)


def _pid_of(element):
    if element is None:
        return None
    try:
        err, pid = AS.AXUIElementGetPid(element, None)
    except Exception:
        return None
    return int(pid) if err == 0 else None


def _name_of(pid):
    if pid is None:
        return None
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return str(app.localizedName()) if app else f"pid {pid}"


def _label(pid):
    name = _name_of(pid)
    return "-" if pid is None else f"{name}({pid})"


class Pass:

    def __init__(self):
        self.system_wide = AS.AXUIElementCreateSystemWide()
        AS.AXUIElementSetMessagingTimeout(self.system_wide, AX_MESSAGING_TIMEOUT)
        self.samples = 0
        self.changes = 0
        self.divergent = 0
        self.ax_errors = {}
        self.pairs = {}
        self.multi_frontmost = 0
        self.durations = []
        self.element_waits = []
        self.child = None
        self.next_drive = 0.0
        self.driven = 0

    def sample(self) -> tuple:
        workspace = NSWorkspace.sharedWorkspace()

        front = workspace.frontmostApplication()
        front_pid = int(front.processIdentifier()) if front else None

        menubar = workspace.menuBarOwningApplication()
        menubar_pid = int(menubar.processIdentifier()) if menubar else None

        started = time.monotonic()
        err, focused_app = _ax_get(self.system_wide, "AXFocusedApplication")
        duration = time.monotonic() - started
        ax_pid = _pid_of(focused_app)

        # Timed separately, and deliberately without a messaging timeout of
        # its own: this is get_focus()'s *second* read, and the element it
        # asks - whether it came from the system-wide read or from
        # AXUIElementCreateApplication - carries no cap, so only the first of
        # the two reads on the key dispatch path is actually bounded.
        role = None
        element_wait = 0.0
        if focused_app is not None:
            started = time.monotonic()
            _, element = _ax_get(focused_app, "AXFocusedUIElement")
            element_wait = time.monotonic() - started
            if element is not None:
                _, value = _ax_get(element, "AXRole")
                role = str(value) if value is not None else None
            self.element_waits.append(element_wait)

        return front_pid, ax_pid, menubar_pid, err, role, duration

    def frontmost_claims(self) -> tuple:
        """Which regular applications answer AXFrontmost true, and which do
        not answer at all.

        Issue #45's second observation is that more than one claims it.  The
        silent ones are reported separately because "did not answer" and
        "answered false" are different facts and a bare False conflates them -
        which is how a busy application reads as a modest one.

        Asked only on a change: it is one cross-process read per application.
        """
        claims, silent = [], []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.activationPolicy() != NSApplicationActivationPolicyRegular:
                continue
            pid = int(app.processIdentifier())
            element = AS.AXUIElementCreateApplication(pid)
            AS.AXUIElementSetMessagingTimeout(element, AX_MESSAGING_TIMEOUT)
            err, value = _ax_get(element, "AXFrontmost")
            if err != 0:
                silent.append(pid)
            elif value:
                claims.append(pid)
        return claims, silent

    def report(self, elapsed, row):
        front_pid, ax_pid, menubar_pid, err, role, duration = row

        verdict = ""
        if err != 0:
            verdict = f"  AX-ERR {err} after {duration * 1000:.0f} ms"
        elif front_pid != ax_pid:
            verdict = "  << DIVERGENT"

        line = (f"[{elapsed:6.1f}s] NSWorkspace={_label(front_pid):<28}"
                f"AX={_label(ax_pid):<28}{verdict}")
        if menubar_pid != front_pid:
            line += f"  menubar={_label(menubar_pid)}"
        if role:
            line += f"  element={role}"
        print(line, flush=True)

        claims, silent = self.frontmost_claims()
        if len(claims) != 1 or (front_pid is not None and claims != [front_pid]):
            names = ", ".join(_label(pid) for pid in claims) or "nobody"
            line = f"           AXFrontmost claimed by: {names}"
            if silent:
                line += ("   no answer from: "
                         + ", ".join(_label(pid) for pid in silent))
            print(line, flush=True)
            if len(claims) > 1:
                self.multi_frontmost += 1

    def drive(self):
        """Spawn one throwaway application, or reap the last one.

        Returns without waiting: the sampling loop is what has to keep
        running, since the transition being measured is over in a few frames.
        """
        if self.child is not None:
            if self.child.poll() is None:
                return
            self.child = None
            self.next_drive = time.monotonic() + DRIVE_GAP
            return

        if time.monotonic() < self.next_drive:
            return

        self.child = subprocess.Popen(
            [sys.executable, "-c", CHILD_SOURCE, str(DRIVE_LIFETIME)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONWARNINGS": "ignore"})
        self.driven += 1

    def run(self, seconds: float, interval: float, driving: bool = False):
        print(__doc__.split("\n\n")[0])
        if driving:
            print(f"Sampling every {interval:g}s for {seconds:g}s, driving the "
                  f"switches with a throwaway application.\n"
                  f"Switch applications yourself as well - real ones take "
                  f"paths a throwaway does not.\n")
        else:
            print(f"Sampling every {interval:g}s for {seconds:g}s.  "
                  f"Switch applications while it runs.\n")

        start = time.monotonic()
        previous = None
        while True:
            elapsed = time.monotonic() - start
            if elapsed > seconds:
                break

            if driving:
                self.drive()

            row = self.sample()
            front_pid, ax_pid, _, err, _, duration = row

            self.samples += 1
            self.durations.append((duration, err))
            self.ax_errors[err] = self.ax_errors.get(err, 0) + 1
            if err == 0 and front_pid != ax_pid:
                self.divergent += 1
                key = (_label(front_pid), _label(ax_pid))
                self.pairs[key] = self.pairs.get(key, 0) + 1

            if row[:4] != (previous[:4] if previous else None):
                self.changes += 1
                self.report(elapsed, row)
                previous = row

            # NOT time.sleep: NSWorkspace's idea of the frontmost application
            # is refreshed by run-loop callbacks and by nothing else, so a
            # sleeping sampler reads the process-start snapshot forever and
            # every transition after the first looks like a divergence.  The
            # rule is doc/dev/testing.md's, and this pass walked into it once.
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(interval))

        self.summary()

    def summary(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()

        driven = f", {self.driven} driven by this pass" if self.driven else ""
        print(f"\n{self.samples} samples, {self.changes} transitions{driven}.")

        slow = sorted(self.durations, reverse=True)[:3]
        if slow:
            worst = ", ".join(f"{d * 1000:.0f} ms (err {e})" for d, e in slow)
            print(f"Slowest AXFocusedApplication reads - {worst}"
                  f"   (the messaging timeout is {AX_MESSAGING_TIMEOUT * 1000:.0f} ms)")

        if self.element_waits:
            worst = sorted(self.element_waits, reverse=True)[:3]
            print("Slowest AXFocusedUIElement reads - "
                  + ", ".join(f"{w * 1000:.0f} ms" for w in worst)
                  + "   (uncapped: get_focus() sets a messaging timeout on the"
                    " system-wide element only)")

        errors = ", ".join(f"{code}: {count}"
                           for code, count in sorted(self.ax_errors.items()))
        print(f"AXFocusedApplication result codes - {errors}")
        print("   (0 is success.  -25204 is kAXErrorCannotComplete, which "
              "arrives two ways: instantly, in a process with no "
              "NSApplication, and after the full messaging timeout, when the "
              "application being asked did not answer in time.  The durations "
              "above tell them apart.)")

        if not self.divergent:
            print("NSWorkspace and AXFocusedApplication named the same "
                  "application in every sample where the AX read succeeded.")
        else:
            print(f"DIVERGENT in {self.divergent} of {self.samples} samples:")
            for (front, ax), count in sorted(self.pairs.items(),
                                             key=lambda kv: -kv[1]):
                print(f"  NSWorkspace={front:<28} AX={ax:<28} x{count}")

        if self.multi_frontmost:
            print(f"More than one application claimed AXFrontmost on "
                  f"{self.multi_frontmost} of {self.changes} transitions.")


def main():
    parser = argparse.ArgumentParser(
        description="Measure whether NSWorkspace and the Accessibility API "
                    "agree about which application is in front.")
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="how long to sample for (default 60)")
    parser.add_argument("--interval", type=float, default=0.2,
                        help="seconds between samples (default 0.2)")
    parser.add_argument("--drive", action="store_true",
                        help="produce the switches too, with a throwaway "
                             "application of this pass's own, so the run is "
                             "conclusive without anyone at the keyboard")
    args = parser.parse_args()

    # The whole measurement depends on this: see the module docstring.  An
    # accessory application never becomes frontmost, so creating one does not
    # perturb what is being measured.
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    if not AS.AXIsProcessTrusted():
        sys.exit("This process does not hold the Accessibility permission - "
                 "grant it to the terminal (or IDE) running this, and rerun.")

    Pass().run(args.seconds, args.interval, args.drive)


if __name__ == "__main__":
    main()
