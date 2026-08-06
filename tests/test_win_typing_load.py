"""Typing latency and event integrity under fast input and load - live.

This is the in-process app harness doc/dev/testing.md describes: the real
WinInputHook, a real Keymap with a real config, and the real Windows focus
provider, all hosted in the test process, with the "user" played by *untagged*
SendInput (dwExtraInfo 0, which the hook classifies as physical). A probe
window records what actually reaches an app.

Why it matters: WH_KEYBOARD_LL is synchronous. Every millisecond spent in the
callback is a millisecond of typing lag, and if the callback overruns
LowLevelHooksTimeout (300 ms by default) Windows silently unhooks Keyhac
mid-sentence. "Typing feel" is that number, so this measures it instead of
asking someone to type and shrug.

Safety: only F13-F24 are bound and only F13-F24 are ever injected. No human
config uses them, they produce no characters, and a concurrently running
production Keyhac is unaffected - its hook sits later in the LL chain. Every
test skips rather than fails if the environment refuses keyboard focus, since
without focus the injected keys would land in someone else's window.
"""

import ctypes
import statistics
import sys
import threading
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.core.keymap import Keymap  # noqa: E402
from keyhac.core.vk import init_key_names  # noqa: E402
from keyhac.platform.win import hook as win_hook  # noqa: E402
from keyhac.platform.win.focus import WinFocusProvider  # noqa: E402
from keyhac.platform.win.hook import WinInputHook  # noqa: E402
from keyhac.platform.win.window import WinWindow  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.argtypes = []
user32.GetFocus.restype = wintypes.HWND
user32.PeekMessageW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

# The only keys this module ever touches.
VK_F13, VK_F14, VK_F15, VK_F16 = 0x7C, 0x7D, 0x7E, 0x7F
VK_F17, VK_F18, VK_F19, VK_F20 = 0x80, 0x81, 0x82, 0x83

# Windows drops a low-level hook whose callback overruns this (ms). It is
# readable from HKLM\...\Control Panel\Desktop\LowLevelHooksTimeout; 300 is
# the default when the value is absent.
LOW_LEVEL_HOOKS_TIMEOUT_MS = 300


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]


class KeyProbe:
    """A window recording the vk of every key-down that reaches it.

    Module-scoped on purpose: re-registering the class from a second instance
    leaves lpfnWndProc pointing at the first instance's freed thunk, and the
    next message faults the interpreter (doc/dev/testing.md).
    """

    def __init__(self):
        self.keys: list[int] = []

        def _proc(hwnd, msg, wparam, lparam):
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self.keys.append(int(wparam))
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(_proc)  # must outlive the window
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "KeyhacTypingLoadProbe"
        user32.RegisterClassW(ctypes.byref(wc))  # repeat registration: benign
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        self.hwnd = user32.CreateWindowExW(
            0, "KeyhacTypingLoadProbe", "typing load probe",
            WS_OVERLAPPEDWINDOW, 60, 60, 360, 120,
            None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW: {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, 5)  # SW_SHOW

    def pump(self, seconds: float) -> None:
        """Drain the message queue. This thread owns the hook, so pumping here
        is also what delivers the hook callbacks."""
        end = time.monotonic() + seconds
        msg = ctypes.create_string_buffer(48)
        while time.monotonic() < end:
            while user32.PeekMessageW(msg, None, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.001)

    def destroy(self) -> None:
        user32.DestroyWindow(self.hwnd)


def post_real(vk: int, down: bool) -> None:
    """Inject a key the hook will classify as physical (dwExtraInfo 0)."""
    inputs = (win_hook.INPUT * 1)()
    flags = 0 if down else win_hook.KEYEVENTF_KEYUP
    scan = win_hook.user32.MapVirtualKeyW(vk, win_hook.MAPVK_VK_TO_VSC)
    inputs[0].type = win_hook.INPUT_KEYBOARD
    inputs[0].union.ki = win_hook.KEYBDINPUT(vk, scan, flags, 0, 0)
    win_hook.user32.SendInput(1, inputs, ctypes.sizeof(win_hook.INPUT))


# A real config file, shaped like one a user would write, over keys no human
# binds. Loaded through Config exactly as ~/.keyhac/config.py is.
CONFIG_SOURCE = """
def configure(keymap):
    kt = keymap.define_keytable(focus_path_pattern="*")
    kt["F13"] = "F14"                 # plain remap
    kt["Ctrl-F15"] = "F16"            # modifier combination
    kt["F17"] = "F18", "F19"          # sequence output
    keymap.define_modifier("F20", "User0")
    kt["User0-F21"] = "F22"           # user-modifier combination
"""


class Harness:
    """Real hook + real engine + real focus provider, hosted in-process."""

    def __init__(self, probe, tmp_path):
        self.probe = probe
        self.hook = WinInputHook()
        self.focus_provider = WinFocusProvider()

        config = tmp_path / "config.py"
        config.write_text(CONFIG_SOURCE)
        self.keymap = Keymap(self.hook, self.focus_provider, "windows",
                             config_path=str(config), template_path=str(config))
        self.keymap.configure()

        self.callback_ms: list[float] = []
        self.restored = 0

    def _on_key(self, event):
        t0 = time.perf_counter()
        try:
            return self.keymap.on_key_event(event)
        finally:
            self.callback_ms.append((time.perf_counter() - t0) * 1000.0)

    def __enter__(self):
        # Re-assert focus per test: a heavy run can cost the probe its focus,
        # and without focus the injected keys land in someone else's window.
        WinWindow(self.probe.hwnd).activate()
        user32.SetFocus(self.probe.hwnd)
        self.probe.pump(0.2)
        if user32.GetFocus() != self.probe.hwnd:
            pytest.skip("environment refused keyboard focus")

        self.hook.install(self._on_key, self._on_restored)
        self.probe.pump(0.1)
        self.callback_ms.clear()
        self.probe.keys.clear()
        return self

    def __exit__(self, *exc):
        self.hook.uninstall()

    def _on_restored(self):
        self.restored += 1

    def type_keys(self, sequence, interval, settle=1.0):
        """Play `sequence` of (vk, down) from a worker thread while this
        thread pumps, exactly as an app's UI thread would."""
        done = threading.Event()

        def sender():
            for vk, down in sequence:
                post_real(vk, down)
                time.sleep(interval)
            done.set()

        thread = threading.Thread(target=sender, daemon=True)
        thread.start()
        while not done.is_set():
            self.probe.pump(0.02)
        self.probe.pump(settle)
        thread.join(timeout=5.0)

    def stats(self):
        samples = self.callback_ms
        assert samples, "the hook produced no callbacks at all"
        ordered = sorted(samples)
        return {
            "n": len(samples),
            "p50": statistics.median(ordered),
            "p95": ordered[int(len(ordered) * 0.95)] if len(ordered) > 20
            else ordered[-1],
            "max": ordered[-1],
        }


def taps(vk, count):
    return [(vk, d) for _ in range(count) for d in (True, False)]


@pytest.fixture(scope="module")
def probe():
    init_key_names("windows", "ansi")
    probe = KeyProbe()
    WinWindow(probe.hwnd).activate()
    user32.SetFocus(probe.hwnd)
    probe.pump(0.3)
    if user32.GetFocus() != probe.hwnd:
        probe.destroy()
        pytest.skip("environment refused keyboard focus")
    yield probe
    probe.destroy()


@pytest.fixture
def harness(probe, tmp_path):
    h = Harness(probe, tmp_path)
    with h:
        yield h


class SystemLoad:
    """Busy the machine from *other processes* - the realistic case.

    Deliberately not threads: in-process Python threads contend our own GIL,
    which measures something else entirely (see TestContention). A user
    compiling or on a video call loads the scheduler, not Keyhac's GIL.
    """

    def __init__(self, processes=None, seconds=30):
        import os
        self.n = processes or max(2, (os.cpu_count() or 4) - 1)
        self.seconds = seconds
        self._procs = []

    def __enter__(self):
        import subprocess
        code = ("import time\n"
                f"end = time.monotonic() + {self.seconds}\n"
                "x = 0\n"
                "while time.monotonic() < end:\n"
                "    for _ in range(100000): x = (x * 31 + 7) & 0xFFFFFFFF\n")
        for _ in range(self.n):
            self._procs.append(subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(0.5)  # let them reach steady state
        return self

    def __exit__(self, *exc):
        for p in self._procs:
            p.kill()
        for p in self._procs:
            p.wait(timeout=5)


class GILLoad:
    """One CPU-bound Python thread inside our own process.

    This is what a config does when a ThreadedAction runs heavy pure-Python
    work: it competes for the GIL with the hook callback, which is also
    Python. Bounded to one thread so the number means something.
    """

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        x = 0
        while not self._stop.is_set():
            for _ in range(10000):
                x = (x * 31 + 7) & 0xFFFFFFFF

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5.0)


def report(name, stats):
    print(f"\n  {name}: n={stats['n']} p50={stats['p50']:.2f}ms "
          f"p95={stats['p95']:.2f}ms max={stats['max']:.2f}ms "
          f"(budget {LOW_LEVEL_HOOKS_TIMEOUT_MS}ms)")


class TestLatency:

    def test_callback_latency_at_a_fast_typing_rate(self, harness):
        """~60 keys/s sustained - faster than anyone types prose."""
        harness.type_keys(taps(VK_F13, 60), interval=0.008)
        stats = harness.stats()
        report("fast typing", stats)
        assert stats["max"] < LOW_LEVEL_HOOKS_TIMEOUT_MS, (
            "a callback overran the timeout; Windows would have unhooked us")
        assert stats["p95"] < 20.0, f"p95 {stats['p95']:.2f}ms is felt as lag"
        assert harness.restored == 0, "the hook was dropped and re-installed"

    def test_callback_latency_under_system_load(self, harness):
        """Every core busy in other processes, as when a build is running."""
        with SystemLoad(seconds=20):
            harness.type_keys(taps(VK_F13, 60), interval=0.008)
        stats = harness.stats()
        report("under system load", stats)
        assert stats["max"] < LOW_LEVEL_HOOKS_TIMEOUT_MS
        assert stats["p95"] < 50.0, (
            f"p95 {stats['p95']:.2f}ms under load is felt as lag")
        assert harness.restored == 0

    def test_callback_latency_in_a_burst(self, harness):
        """No pacing at all - the auto-repeat / paste-into-terminal case."""
        harness.type_keys(taps(VK_F13, 100), interval=0.0)
        stats = harness.stats()
        report("burst", stats)
        assert stats["max"] < LOW_LEVEL_HOOKS_TIMEOUT_MS
        assert harness.restored == 0


class TestIntegrity:

    def test_every_keystroke_is_translated_in_a_burst(self, harness):
        """The remap must hold for all 100 taps: the app sees F14, never F13."""
        harness.type_keys(taps(VK_F13, 100), interval=0.0)
        seen = harness.probe.keys
        assert VK_F13 not in seen, "an untranslated F13 leaked through"
        assert seen.count(VK_F14) == 100, (
            f"expected 100 F14, saw {seen.count(VK_F14)}")

    def test_order_is_preserved_across_mixed_keys(self, harness):
        """Interleave two remapped keys; the app must see them in order."""
        sequence = []
        expected = []
        for i in range(30):
            vk = VK_F13 if i % 2 == 0 else VK_F17
            sequence += [(vk, True), (vk, False)]
            expected += [VK_F14] if vk == VK_F13 else [VK_F18, VK_F19]

        harness.type_keys(sequence, interval=0.002)
        assert harness.probe.keys == expected

    def test_modifier_combination_survives_fast_repeat(self, harness):
        sequence = [(0xA2, True)]  # LCtrl down
        sequence += taps(VK_F15, 40)
        sequence += [(0xA2, False)]
        harness.type_keys(sequence, interval=0.002)
        assert harness.probe.keys.count(VK_F16) == 40
        assert VK_F15 not in harness.probe.keys


class TestContention:

    @pytest.mark.parametrize("switch_interval", [None, 0.001])
    def test_cpu_bound_python_in_our_own_process_costs_latency(
            self, harness, switch_interval):
        """A ThreadedAction doing heavy pure-Python work competes for the GIL
        with the hook callback, because the callback is Python too.

        This is the one load shape that pushes a callback past
        LowLevelHooksTimeout on this machine, so it is characterized rather
        than asserted away - the numbers are the point. The parametrization
        checks whether a shorter GIL switch interval is a usable mitigation.
        """
        previous = sys.getswitchinterval()
        if switch_interval is not None:
            sys.setswitchinterval(switch_interval)
        try:
            with GILLoad():
                harness.type_keys(taps(VK_F13, 40), interval=0.008)
            stats = harness.stats()
        finally:
            sys.setswitchinterval(previous)

        report(f"one CPU-bound Python thread in-process "
               f"(switchinterval={switch_interval or previous})", stats)
        # The hook must still be alive: check_health re-installs it if Windows
        # did drop it, and that re-install is the thing users would notice.
        assert harness.restored == 0
        assert stats["p50"] < LOW_LEVEL_HOOKS_TIMEOUT_MS, (
            "even the median keystroke overruns the hook timeout")

    def test_worker_holding_the_input_context_stalls_the_hook(self, harness):
        """A documented sharp edge, pinned so it cannot get worse silently.

        InputContext takes the same RLock the hook callback needs, and it is
        documented as safe to use from a worker thread. While a worker holds
        it, typing is blocked - so a config that holds one for longer than
        LowLevelHooksTimeout will get Keyhac unhooked. This asserts the stall
        tracks the hold, and that a short hold stays well inside the budget.
        """
        hold = 0.05
        started = threading.Event()

        def worker():
            with harness.keymap.get_input_context():
                started.set()
                time.sleep(hold)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        started.wait(timeout=2.0)
        harness.type_keys(taps(VK_F13, 4), interval=0.002)
        thread.join(timeout=2.0)

        stats = harness.stats()
        report("input context held 50ms", stats)
        assert stats["max"] >= hold * 1000 * 0.5, (
            "expected the held lock to show up as callback latency")
        assert stats["max"] < LOW_LEVEL_HOOKS_TIMEOUT_MS
