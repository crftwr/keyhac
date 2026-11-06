"""What the keyboard hook costs on this Windows machine and architecture.

    python tools/win_hook_latency_pass.py

THE QUESTION (issue #318).  Keyhac for Windows ships as x64, and on Windows on
Arm the OS runs that build through emulation.  WH_KEYBOARD_LL is synchronous,
so the callback's duration is typing lag, and a callback that overruns
LowLevelHooksTimeout (300 ms unless configured) can have Windows remove the
hook.  How close does emulation come to that?

WHY NOT tests/test_win_typing_load.py.  Its keys go to a window in the test's
own process, and `WinFocusProvider.get_focus` skips the focused-element read
for Keyhac's own windows.  The everyday case is another application in front,
where every key down and up makes a cross-process UI Automation call and a
focus change adds the path walk.  So this measures against other processes'
windows: a classic Win32 one, WinUI (Notepad) and Chromium (Edge).

HOW IT ANSWERS.  The real WinInputHook, Keymap, config loading and focus
provider, hosted in this process and pumped as the application's main thread
would, with check_health on its 100 ms tick.  Input is untagged SendInput,
which the hook reads as physical.  The time is the whole of `_hook_proc`.  The
same script under an x64 and an ARM64 interpreter on one machine gives the
comparison; it reports which one it is in.

WHAT IT DRIVES.  Its own throwaway windows, and disposable Edge and Notepad
windows.  Only F13 is injected.  It takes the foreground repeatedly, so it
belongs in a VM, in the desktop session.

Paste the output back verbatim.
"""

import sys

if sys.platform != "win32":
    sys.exit(f"{__file__} is a Windows pass; this is {sys.platform}.")

import ctypes                                                        # noqa: E402
import os                                                            # noqa: E402
import pathlib                                                       # noqa: E402
import platform                                                      # noqa: E402
import subprocess                                                    # noqa: E402
import sysconfig                                                     # noqa: E402
import tempfile                                                      # noqa: E402
import threading                                                     # noqa: E402
import time                                                          # noqa: E402
from ctypes import wintypes                                          # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from win_probe_cost_pass import AppProbe, Probe, describe_hwnd       # noqa: E402

from keyhac.core.keymap import Keymap                                # noqa: E402
from keyhac.core.vk import init_key_names                            # noqa: E402
from keyhac.platform.win import hook as win_hook                     # noqa: E402
from keyhac.platform.win.focus import WinFocusProvider               # noqa: E402
from keyhac.platform.win.hook import WinInputHook                    # noqa: E402
from keyhac.platform.win.window import WinWindow                     # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.IsWow64Process2.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.USHORT),
                                     ctypes.POINTER(wintypes.USHORT)]
kernel32.IsWow64Process2.restype = wintypes.BOOL

#: The one key injected, and so the one whose callbacks are samples. What the
#: config turns it into comes back tagged as our own and is not counted.
VK_F13 = 0x7C

#: Windows' default for a low-level hook's deadline. What it does past this is
#: not certain - doc/dev/testing.md records a build that survived a single
#: 0.6 s stall - so this is the line to stay away from, not a cliff.
HOOK_TIMEOUT_MS = 300.0
#: WinInputHook's own warning threshold.
SLOW_MS = WinInputHook.SLOW_CALLBACK_SECONDS * 1000

#: Shaped like a user's: every key table's condition is evaluated on a focus
#: change, and F13 is remapped, so a callback includes the output's SendInput.
CONFIG_SOURCE = """
def configure(keymap):
    kt = keymap.define_keytable(focus_path_pattern="*")
    kt["F13"] = "F14"
    edit = keymap.define_keytable(focus_path_pattern="*/Edit()*", app="notepad*")
    edit["F15"] = "F16"
    code = keymap.define_keytable(app="msedge*", title="*keyhac*")
    code["F15"] = "F16"
"""

MACHINES = {0x0000: "unknown", 0x014C: "x86", 0x8664: "x64", 0xAA64: "ARM64"}

failures = []


def section(title):
    print(f"\n=== {title} ===", flush=True)


def note(text):
    print(f"       {text}", flush=True)


def percentile(ordered, fraction):
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def summarize(label, samples, expected=None):
    """One line of percentiles; a run that never reached the hook is a failure."""
    if not samples:
        print(f"[FAIL] {label}: no callback at all", flush=True)
        failures.append(label)
        return
    ordered = sorted(samples)
    near = sum(1 for value in ordered if value >= SLOW_MS)
    over = sum(1 for value in ordered if value >= HOOK_TIMEOUT_MS)
    lost = f"  lost {expected - len(ordered)}" if expected is not None else ""
    print(f"       {label:<44} n={len(ordered):<5}"
          f"p50 {percentile(ordered, .50):7.2f}  p95 {percentile(ordered, .95):7.2f}  "
          f"p99 {percentile(ordered, .99):7.2f}  max {ordered[-1]:7.2f} ms  "
          f">={SLOW_MS:g}ms {near}  >={HOOK_TIMEOUT_MS:g}ms {over}{lost}", flush=True)
    if expected is not None and len(ordered) != expected:
        failures.append(f"{label}: {expected - len(ordered)} key events never "
                        f"reached the hook")


# -- which build this is ------------------------------------------------------

def report_environment():
    section("environment")
    process, native = wintypes.USHORT(), wintypes.USHORT()
    if kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(process),
                                ctypes.byref(native)):
        note(f"OS architecture: {MACHINES.get(native.value, hex(native.value))}")
    note(f"Python build:    {sysconfig.get_platform()}  "
         f"(platform.machine() {platform.machine()}, sys.winver {sys.winver})")
    emulated = (native.value == 0xAA64 and sysconfig.get_platform() != "win-arm64")
    note(f"so this process is {'EMULATED' if emulated else 'native'}")
    note(f"Windows {platform.version()}, {os.cpu_count()} logical processors")
    note(sys.version)


def report_interpreter_speed():
    """The two numbers emulation shows up in without any input involved."""
    section("the interpreter on its own")
    runs = []
    for _ in range(5):
        started = time.perf_counter()
        x = 0
        for _ in range(2_000_000):
            x = (x * 31 + 7) & 0xFFFFFFFF
        runs.append((time.perf_counter() - started) * 1000)
    note(f"2M rounds of pure Python: best {min(runs):.0f} ms, worst {max(runs):.0f} ms")

    code = ("import keyhac.core.keymap, keyhac.platform.win.hook, "
            "keyhac.platform.win.uielement, numpy, puikit")
    timings = []
    for _ in range(4):
        started = time.perf_counter()
        said = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        timings.append((time.perf_counter() - started) * 1000)
        if said.returncode:
            note(f"importing failed: {said.stderr[-400:]}")
            break
    note("new process importing the engine, numpy and PuiKit: "
         + ", ".join(f"{value:.0f}" for value in timings)
         + " ms (the first is cold)")


# -- the application, hosted here ---------------------------------------------

class TimedHook(WinInputHook):
    """WinInputHook, timing all of `_hook_proc` for the keys being measured."""

    def __init__(self):
        super().__init__()
        self.samples = []

    def _hook_proc(self, n_code, w_param, l_param):
        started = time.perf_counter()
        result = super()._hook_proc(n_code, w_param, l_param)
        taken = (time.perf_counter() - started) * 1000
        if n_code >= 0:
            kbd = ctypes.cast(l_param,
                              ctypes.POINTER(win_hook.KBDLLHOOKSTRUCT)).contents
            if int(kbd.vkCode) == VK_F13 and int(kbd.dwExtraInfo) == 0:
                self.samples.append(taken)
        return result


class App:
    """The wiring main() does, with this thread as the main thread."""

    def __init__(self, directory):
        config = pathlib.Path(directory) / "config.py"
        config.write_text(CONFIG_SOURCE, encoding="utf-8")
        init_key_names("windows", "ansi")
        self.hook = TimedHook()
        self.keymap = Keymap(self.hook, WinFocusProvider(), "windows",
                             config_path=str(config), template_path=str(config))
        self.keymap.configure()
        self.restored = 0
        self._next_health = 0.0
        self.hook.install(self.keymap._on_key_event, self._on_restored)

    def _on_restored(self):
        self.restored += 1

    def close(self):
        self.hook.uninstall()

    def pump(self, seconds=0.0, until=None):
        """Pump as the main thread does, running the health tick. Hook
        callbacks arrive in here."""
        end = time.monotonic() + seconds
        msg = ctypes.create_string_buffer(48)
        while (until is not None and not until.is_set()) or time.monotonic() < end:
            while user32.PeekMessageW(msg, None, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            now = time.monotonic()
            if now >= self._next_health:
                self._next_health = now + 0.1
                self.hook.check_health()
            time.sleep(0.001)

    def on_worker(self, work, settle=0.0):
        """Run `work` off this thread and pump meanwhile: input sent from the
        hook's own thread would not show what a real keystroke does."""
        done = threading.Event()
        kept = {}

        def run():
            try:
                kept["result"] = work()
            finally:
                done.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.pump(until=done)
        self.pump(settle)
        thread.join(5.0)
        return kept.get("result")

    def to_front(self, hwnd, settle=0.5):
        """Whether `hwnd` was brought in front, by the product's own route."""
        self.on_worker(WinWindow(hwnd).activate, settle=settle)
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def post_real(vk, down):
    """A key the hook classifies as physical (dwExtraInfo 0)."""
    inputs = (win_hook.INPUT * 1)()
    flags = 0 if down else win_hook.KEYEVENTF_KEYUP
    scan = win_hook.user32.MapVirtualKeyW(vk, win_hook.MAPVK_VK_TO_VSC)
    inputs[0].type = win_hook.INPUT_KEYBOARD
    inputs[0].union.ki = win_hook.KEYBDINPUT(vk, scan, flags, 0, 0)
    win_hook.user32.SendInput(1, inputs, ctypes.sizeof(win_hook.INPUT))


def type_keys(app, taps, interval):
    """`taps` presses of F13; returns the callback times they cost."""
    app.hook.samples.clear()

    def sender():
        for _ in range(taps):
            for down in (True, False):
                post_real(VK_F13, down)
                time.sleep(interval)

    app.on_worker(sender, settle=0.5)
    return list(app.hook.samples)


# -- what loads it ------------------------------------------------------------

class SystemLoad:
    """Every core busy in other processes, as when a build runs."""

    def __init__(self, seconds=60):
        code = ("import time\n"
                f"end = time.monotonic() + {seconds}\n"
                "x = 0\n"
                "while time.monotonic() < end:\n"
                "    for _ in range(100000): x = (x * 31 + 7) & 0xFFFFFFFF\n")
        self.argv = [sys.executable, "-c", code]
        self.processes = []

    def __enter__(self):
        for _ in range(os.cpu_count() or 4):
            self.processes.append(subprocess.Popen(self.argv))
        time.sleep(1.0)
        return self

    def __exit__(self, *exc):
        for process in self.processes:
            process.kill()
        for process in self.processes:
            process.wait(timeout=5)


class GILLoad:
    """One CPU-bound Python thread here: a ThreadedAction doing heavy work."""

    def __enter__(self):
        self._stop = threading.Event()

        def spin():
            x = 0
            while not self._stop.is_set():
                for _ in range(10000):
                    x = (x * 31 + 7) & 0xFFFFFFFF

        self._thread = threading.Thread(target=spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(5.0)


# -- the measurements ---------------------------------------------------------

def measure_target(app, label, hwnd, away):
    """Everything that varies with which application is in front."""
    section(label)
    if not app.to_front(hwnd):
        now = describe_hwnd(int(user32.GetForegroundWindow() or 0))
        print(f"[FAIL] {label}: could not bring it in front; {now} is", flush=True)
        failures.append(f"{label}: not in front")
        return
    note(f"in front: {describe_hwnd(hwnd)}")
    focus = app.keymap._focus_provider.get_focus()
    if focus is not None:
        note(f"path: {focus.path}")

    # The first key after focus arrives is where the path walk runs, and so
    # the most expensive callback ordinary use has.
    first, rest = [], []
    for _ in range(15):
        if not app.to_front(away) or not app.to_front(hwnd):
            continue
        app.hook.samples.clear()
        app.on_worker(lambda: post_real(VK_F13, True), settle=0.15)
        first += app.hook.samples
        app.hook.samples.clear()
        app.on_worker(lambda: post_real(VK_F13, False), settle=0.15)
        rest += app.hook.samples
    summarize("first key after a focus change (path walk)", first)
    summarize("its key up (read + compare)", rest)

    app.to_front(hwnd)
    summarize("steady, 30 keys/s", type_keys(app, 300, 0.016), expected=600)
    summarize("burst, no pacing", type_keys(app, 200, 0.0), expected=400)

    # Long enough apart for whatever the machine keeps warm to go cold.
    idle = []
    for _ in range(8):
        app.pump(4.0)
        app.hook.samples.clear()
        app.on_worker(lambda: post_real(VK_F13, True), settle=0.1)
        idle += app.hook.samples
        app.on_worker(lambda: post_real(VK_F13, False), settle=0.05)
    summarize("after 4 s idle", idle)

    with SystemLoad():
        app.to_front(hwnd)
        summarize("every core busy in other processes",
                  type_keys(app, 150, 0.016), expected=300)

    app.to_front(hwnd)
    with GILLoad():
        summarize("one CPU-bound Python thread in-process",
                  type_keys(app, 60, 0.016), expected=120)
    note(f"hook re-installs so far: {app.restored}")


def main():
    report_environment()
    report_interpreter_speed()

    probes = []
    page = None
    with tempfile.TemporaryDirectory() as directory:
        app = App(directory)
        try:
            away = Probe("KEYHAC-HOOK-LATENCY-AWAY", 40)
            classic = Probe("KEYHAC-HOOK-LATENCY-CLASSIC", 480)
            probes += [away, classic]
            app.pump(0.5)
            measure_target(app, "A. classic Win32 window, another process",
                           classic.hwnd, away.hwnd)

            notepad = AppProbe("WinUI", ["notepad.exe"])
            probes.append(notepad)
            app.pump(0.5)
            if notepad.hwnd:
                measure_target(app, "B. WinUI (Notepad)", notepad.hwnd, away.hwnd)
            else:
                print("[FAIL] Notepad gave no window", flush=True)
                failures.append("Notepad gave no window")

            handle, page = tempfile.mkstemp(suffix=".html", prefix="keyhac_hook_")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write("<!doctype html><title>keyhac hook latency</title>"
                             "<body><input autofocus size=40></body>")
            edge = AppProbe("Chromium", [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                "--new-window", "--no-first-run", page])
            probes.append(edge)
            app.pump(2.0)
            if edge.hwnd:
                measure_target(app, "C. Chromium (Edge, a page input)",
                               edge.hwnd, away.hwnd)
            else:
                print("[FAIL] Edge gave no window", flush=True)
                failures.append("Edge gave no window")

            section("the hook at the end")
            app.to_front(classic.hwnd)
            summarize("still called", type_keys(app, 20, 0.016), expected=40)
            note(f"re-installs by check_health over the whole pass: {app.restored}")
        finally:
            app.close()
            for probe in probes:
                probe.close()
            if page and os.path.exists(page):
                os.unlink(page)

    print(f"\n{'ALL RAN' if not failures else 'PROBLEMS: ' + '; '.join(failures)}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
