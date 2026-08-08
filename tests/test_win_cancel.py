"""Esc cancellation through the real Windows hook.

`tests/test_cancel.py` covers the engine's half by handing `on_key_event` a
`KeyEvent` directly, and its docstring says why it can go no further: Keyhac's
own translated output "never reaches on_key_event at all (the platform layer
drops it on its own tag)".  That sentence is a claim about
`keyhac/platform/win/hook.py`, and nothing was checking it.

So this injects real Esc through real `SendInput` at a real `WH_KEYBOARD_LL`
hook, varying only `dwExtraInfo`:

    0                      physical, as far as the hook can tell
    EXTRA_INFO_OWN         Keyhac's own translated output
    EXTRA_INFO_REPLAY      a macro replaying a recorded Esc

Only the first may stop an action - an action that presses Escape to dismiss a
dialog must not thereby kill itself - and only the one that stops something is
swallowed, since swallowing every Esc would change what the focused
application sees.

Needs keyboard focus, and skips rather than lies when the desktop refuses it
(doc/dev/testing.md: guards fire on detected interference, never on a failed
assertion).
"""

import ctypes
import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.core.action import ThreadedAction  # noqa: E402
from keyhac.core.keymap import Keymap  # noqa: E402
from keyhac.core.vk import get_key_names  # noqa: E402
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
user32.GetFocus.restype = wintypes.HWND
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WM_KEYDOWN = 0x0100


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class Probe:
    """A window to hold focus and record what the hook let through.

    Module-scoped, like the typing-load probe and for the same reason: a second
    instance re-registering the class leaves `lpfnWndProc` pointing at the
    first instance's freed thunk, and the next message faults the interpreter.
    """

    def __init__(self):
        self.keys: list[int] = []

        def _proc(hwnd, msg, wparam, lparam):
            if msg == WM_KEYDOWN:
                self.keys.append(int(wparam))
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(_proc)          # must outlive the window
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "KeyhacEscProbe"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, "KeyhacEscProbe", "esc probe", 0x00CF0000,
            60, 60, 380, 140, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW: {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, 5)

    def pump(self, seconds: float) -> None:
        """Drain the queue. This thread owns the hook, so pumping here is also
        what delivers the hook callbacks."""
        end = time.monotonic() + seconds
        msg = ctypes.create_string_buffer(48)
        while time.monotonic() < end:
            while user32.PeekMessageW(msg, None, 0, 0, 1):   # PM_REMOVE
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.001)

    def destroy(self) -> None:
        user32.DestroyWindow(self.hwnd)


def send_escape(vk: int, extra: int) -> None:
    """Inject one Esc down+up carrying `extra` as dwExtraInfo."""
    for flags in (0, win_hook.KEYEVENTF_KEYUP):
        inputs = (win_hook.INPUT * 1)()
        scan = user32.MapVirtualKeyW(vk, win_hook.MAPVK_VK_TO_VSC)
        inputs[0].type = win_hook.INPUT_KEYBOARD
        inputs[0].union.ki = win_hook.KEYBDINPUT(vk, scan, flags, 0, extra)
        user32.SendInput(1, inputs, ctypes.sizeof(win_hook.INPUT))


class Waiter(ThreadedAction):
    def run(self):
        return None


@pytest.fixture(scope="module")
def probe():
    p = Probe()
    yield p
    p.destroy()


class Harness:
    """Real hook + real engine, with the engine's view of each event recorded."""

    def __init__(self, probe, tmp_path):
        self.probe = probe
        self.hook = WinInputHook()
        config = tmp_path / "config.py"
        config.write_text("def configure(keymap):\n    pass\n")
        self.keymap = Keymap(self.hook, WinFocusProvider(), "windows",
                             config_path=str(config), template_path=str(config))
        self.keymap.configure()
        self.seen: list[tuple[int, str]] = []

    def _on_key(self, event):
        self.seen.append((event.vk, event.kind))
        return self.keymap.on_key_event(event)

    def __enter__(self):
        # Re-asserted per test, not once per module: a busy desktop can cost
        # the probe its focus, and the injection would then land in someone
        # else's window and quietly prove nothing.
        WinWindow(self.probe.hwnd).activate()
        user32.SetFocus(self.probe.hwnd)
        self.probe.pump(0.2)
        if user32.GetFocus() != self.probe.hwnd:
            pytest.skip("environment refused keyboard focus")
        self.hook.install(self._on_key, lambda: None)
        self.probe.pump(0.1)
        self.reset()
        return self

    def __exit__(self, *exc):
        self.hook.uninstall()

    def reset(self):
        self.seen.clear()
        self.probe.keys.clear()

    def escape(self, extra):
        """Inject Esc and pump until the hook and the window have had it."""
        send_escape(get_key_names().str_to_vk("Escape"), extra)
        self.probe.pump(0.6)

    @property
    def escape_kinds(self):
        vk = get_key_names().str_to_vk("Escape")
        return [kind for seen_vk, kind in self.seen if seen_vk == vk]

    @property
    def window_saw_escape(self):
        return get_key_names().str_to_vk("Escape") in self.probe.keys


@pytest.fixture
def harness(probe, tmp_path):
    with Harness(probe, tmp_path) as h:
        yield h


def test_physical_escape_stops_the_action_and_is_swallowed(harness):
    action = Waiter()
    with action.cancellable():
        harness.escape(0)
        assert harness.escape_kinds == ["real", "real"], (
            f"the hook misclassified untagged input: {harness.seen}")
        assert action.cancelled() is True
        assert not harness.window_saw_escape, (
            "the Esc that cancelled was still delivered to the application")


def test_keyhacs_own_escape_never_reaches_the_engine(harness):
    """The half test_cancel.py asserts about this layer rather than checking.

    An action that sends Escape to dismiss a dialog would otherwise cancel
    itself on its own output.
    """
    action = Waiter()
    with action.cancellable():
        harness.escape(win_hook.EXTRA_INFO_OWN)
        assert harness.escape_kinds == [], (
            f"translated output reached the engine: {harness.seen}")
        assert action.cancelled() is False
        assert harness.window_saw_escape, "the app never got the injected Esc"


def test_a_replayed_escape_does_not_cancel(harness):
    """A macro replaying an Esc is not a user asking to stop."""
    action = Waiter()
    with action.cancellable():
        harness.escape(win_hook.EXTRA_INFO_REPLAY)
        assert harness.escape_kinds == ["replay", "replay"]
        assert action.cancelled() is False
        assert harness.window_saw_escape


def test_escape_passes_through_when_nothing_is_running(harness):
    """Swallowing every Esc would change what the focused application sees."""
    assert ThreadedAction.cancel_all() == 0, "an action leaked from another test"
    harness.escape(0)
    assert harness.escape_kinds == ["real", "real"]
    assert harness.window_saw_escape
