"""Windows IME provider - live, against a probe window this process owns.

The probe is an EDIT control in a frame of its own, which is what makes the
interesting case testable: the frame and the control resolve to *different*
default IME windows, and only the focused control's carries the live state.
Asking the frame - what this module was written against before the first
Windows pass - reads a flag nothing consumes (doc/dev/testing.md).

The layout is switched on this thread with ActivateKeyboardLayout, so both
halves of the contract are reachable without touching the machine's settings:
an IME layout (Microsoft IME) for the on/off round trip, and a plain one
(en-US) for "there is no IME to turn on".  Each half skips if that layout is
not installed.  Briefly takes keyboard focus; skips if the environment refuses
it, as tests/test_win_send_text.py does.
"""

import ctypes
import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.platform.win.hook import WinInputHook  # noqa: E402
from keyhac.platform.win.ime import WinImeProvider  # noqa: E402
from keyhac.platform.win.window import WinWindow  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)
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
user32.SendMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPVOID]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.PeekMessageW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HKL)]
user32.ActivateKeyboardLayout.argtypes = [wintypes.HKL, wintypes.UINT]
user32.ActivateKeyboardLayout.restype = wintypes.HKL
imm32.ImmGetProperty.argtypes = [wintypes.HKL, wintypes.DWORD]
imm32.ImmGetProperty.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

IGP_CONVERSION = 0x0008
WM_SETTEXT, WM_GETTEXT = 0x000C, 0x000D
VK_A, VK_RETURN = 0x41, 0x0D


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]


def _layouts_with_and_without_ime():
    """The installed layouts, split by whether an IME sits behind them."""
    count = user32.GetKeyboardLayoutList(0, None)
    layouts = (wintypes.HKL * count)()
    user32.GetKeyboardLayoutList(count, layouts)
    with_ime, without = [], []
    for hkl in layouts:
        bucket = with_ime if imm32.ImmGetProperty(hkl, IGP_CONVERSION) else without
        bucket.append(hkl)
    return with_ime, without


class ImeProbe:
    """A frame with one EDIT child - a focused control distinct from its frame."""

    def __init__(self):
        self._proc = WNDPROC(user32.DefWindowProcW)  # must outlive the window
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "KeyhacImeProbe"
        user32.RegisterClassW(ctypes.byref(wc))  # repeat registration: benign
        WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE = 0x00CF0000, 0x40000000, 0x10000000
        ES_MULTILINE = 0x0004
        self.hwnd = user32.CreateWindowExW(
            0, "KeyhacImeProbe", "IME probe", WS_OVERLAPPEDWINDOW,
            60, 60, 320, 140, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW: {ctypes.get_last_error()}")
        self.edit = user32.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | WS_VISIBLE | ES_MULTILINE,
            0, 0, 300, 100, self.hwnd, 1, wc.hInstance, None)
        if not self.edit:
            raise OSError(f"CreateWindowExW(EDIT): {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, 5)  # SW_SHOW

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        msg = ctypes.create_string_buffer(48)
        while time.monotonic() < end:
            while user32.PeekMessageW(msg, None, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.005)

    def require_focus(self) -> None:
        """A busy desktop can take the probe's focus mid-run, and the injection
        then lands in somebody else's window.  That is a skip, not a failure -
        the house rule for the live input tests (doc/dev/testing.md)."""
        if user32.GetFocus() != self.edit:
            pytest.skip("keyboard focus left the probe mid-test")

    def clear(self) -> None:
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.c_wchar_p(""))

    def read(self) -> str:
        buffer = ctypes.create_unicode_buffer(64)
        user32.SendMessageW(self.edit, WM_GETTEXT, 64, buffer)
        return buffer.value

    def type_and_read(self, vks=(VK_A,)) -> str:
        """Type through the IME, commit, and return what the control kept."""
        self.require_focus()
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.c_wchar_p(""))
        events = []
        for vk in (*vks, VK_RETURN):
            events += [(vk, True), (vk, False)]
        WinInputHook().send(events)
        self.pump(0.6)
        self.require_focus()
        return self.read().strip()

    def destroy(self) -> None:
        user32.DestroyWindow(self.hwnd)


@pytest.fixture(scope="module")
def probe():
    probe = ImeProbe()
    WinWindow(probe.hwnd).activate()
    user32.SetFocus(probe.edit)
    probe.pump(0.2)
    if user32.GetFocus() != probe.edit:
        probe.destroy()
        pytest.skip("environment refused keyboard focus")
    saved_layout = user32.GetKeyboardLayout(0)
    yield probe
    user32.ActivateKeyboardLayout(saved_layout, 0)
    probe.destroy()


@pytest.fixture
def provider():
    return WinImeProvider()


@pytest.fixture
def ime_layout(probe):
    """The probe typing under a layout that has an IME (Microsoft IME)."""
    with_ime, _ = _layouts_with_and_without_ime()
    if not with_ime:
        pytest.skip("no IME layout installed (Microsoft IME needed)")
    user32.ActivateKeyboardLayout(with_ime[0], 0)
    probe.pump(0.3)
    yield probe
    # Close it *and* let it settle.  An IME that was just closed still costs
    # the next injection its tail: leaving without the pump made the Japanese
    # case of tests/test_win_send_text.py drop characters about one run in
    # five, which is the "injected input is occasionally lost" hazard in
    # doc/dev/testing.md arriving through a neighbour.
    WinImeProvider().set_status(False)
    probe.pump(0.3)


@pytest.fixture
def plain_layout(probe):
    """The probe typing under a layout with no IME behind it (en-US)."""
    _, without = _layouts_with_and_without_ime()
    if not without:
        pytest.skip("every installed layout has an IME")
    user32.ActivateKeyboardLayout(without[0], 0)
    probe.pump(0.3)
    yield probe


class TestWhatGetsAsked:

    def test_the_focused_control_is_asked_not_the_frame(self, probe, provider):
        """The regression guard for the bug the first Windows pass found.

        A frame and its focused child hand back different default IME windows,
        and the frame's answers are frozen - so resolving the target through
        GetForegroundWindow() alone reads and writes a phantom state.
        """
        assert provider._input_window() == probe.edit != probe.hwnd


class TestUnderAnImeLayout:

    def test_status_is_a_bool_when_an_ime_answers(self, ime_layout, provider):
        assert provider.get_status() in (True, False)

    def test_turning_on_and_off_round_trips(self, ime_layout, provider):
        assert provider.set_status(True) is True
        assert provider.get_status() is True
        assert provider.set_status(False) is True
        assert provider.get_status() is False

    def test_setting_the_state_it_is_already_in_succeeds(self, ime_layout, provider):
        provider.set_status(False)
        assert provider.set_status(False) is True
        assert provider.get_status() is False

    def test_turning_it_on_is_what_the_application_then_composes(
            self, ime_layout, provider):
        """The end-to-end check: the state has to reach the keystrokes.

        Typing 'a' with the IME on yields a kana character, off yields 'a'.
        What is composed depends on the IME's own mode, so this asserts only
        that the result stopped being ASCII - not that it is any one kana.
        """
        assert provider.set_status(True) is True
        composed = ime_layout.type_and_read()
        assert provider.set_status(False) is True
        plain = ime_layout.type_and_read()
        assert plain == "a"
        assert composed and not composed.isascii(), (
            f"IME on produced {composed!r}, expected a composed character")


    def test_send_text_goes_through_an_open_ime_untouched(self, ime_layout,
                                                          provider):
        """Why the config template types literal text with send_text().

        It injects the characters themselves rather than feeding keys to the
        IME, so it needs no IME dance - and the dance a config would reach for
        instead ("off, send, back on") cannot work: the state change is
        immediate while the keys are only queued for the application.
        """
        assert provider.set_status(True) is True
        ime_layout.pump(0.3)          # let the IME finish opening
        ime_layout.require_focus()
        ime_layout.clear()
        WinInputHook().send_text("git status")
        ime_layout.pump(0.8)
        ime_layout.require_focus()
        text = ime_layout.read()
        provider.set_status(False)    # this test is the one that opened it
        ime_layout.pump(0.3)
        assert text == "git status"


class TestUnderALayoutWithNoIme:
    """An open status set here takes, composes nothing, and is dropped at the
    next layout switch - so the honest answers are off and refusal."""

    def test_it_reads_off(self, plain_layout, provider):
        assert provider.get_status() is False

    def test_turning_it_on_fails(self, plain_layout, provider):
        assert provider.set_status(True) is False
        assert provider.get_status() is False

    def test_turning_it_off_succeeds(self, plain_layout, provider):
        assert provider.set_status(False) is True

    def test_nothing_is_composed(self, plain_layout, provider):
        provider.set_status(True)
        assert plain_layout.type_and_read() == "a"


class TestTheSendItself:

    def test_the_send_is_capped_well_under_the_hook_timeout(self):
        """The cap is load-bearing: this runs inside the WH_KEYBOARD_LL
        callback, and exceeding LowLevelHooksTimeout (300 ms) gets the hook
        silently unhooked."""
        from keyhac.platform.win import ime
        assert ime.SEND_TIMEOUT_MS <= 150

    def test_a_query_returns_promptly(self, probe, provider):
        """A responsive IME answers far inside the cap; this is the regression
        guard for accidentally reintroducing a blocking SendMessage."""
        start = time.perf_counter()
        for _ in range(10):
            provider.get_status()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 10 * 50
