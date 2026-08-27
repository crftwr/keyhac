"""Windows keyboard hook - WH_KEYBOARD_LL + SendInput via ctypes.

Reimplements the behavior keyhac-win got from pyauto (pyautocore.pyd):
- low-level keyboard hook installed on the main thread; callbacks are
  delivered while that thread pumps messages; return nonzero to consume
- injection via SendInput() batches, tagged through dwExtraInfo so the hook
  can classify its own events ("own" filtered, "replay" re-processed).
  There is deliberately no counterpart to the macOS defer-real-events
  machinery: SendInput inserts the batch atomically at the tail of the system
  input queue, so ordering against subsequent physical input is guaranteed by
  the OS (see InputHook.send in platform/base.py)
- silent-unhook recovery: Windows removes a hook whose callback exceeds
  LowLevelHooksTimeout (~300 ms) without any notification; a periodic sanity
  check (ported from keyhac-win Keymap.checkSanity) detects modifier-state
  changes that arrived without hook callbacks and re-installs the hook

STATUS: verified live on Windows, including consume decisions on real
(untagged) input, extended-key output flags, the sanity-check re-install
path (provoked by covert unhook - note this Windows 11 build did NOT
remove the hook after a single 0.6 s callback stall), send_text, and
mouse injection + WH_MOUSE_LL classification.  See doc/dev/testing.md.

Every ctypes prototype below is declared explicitly.  This is not optional on
64-bit: the default restype of c_int truncates handles, which is what made
SetWindowsHookExW fail with ERROR_MOD_NOT_FOUND (126) on a truncated HMODULE.
"""

import ctypes
import sys
from typing import Callable, Sequence

from keyhac.platform.base import InputHook, KeyEvent
from keyhac.core.const import (
    MODKEY_ALT, MODKEY_ALT_L, MODKEY_ALT_R,
    MODKEY_CMD, MODKEY_CMD_L, MODKEY_CMD_R,
    MODKEY_CTRL, MODKEY_CTRL_L, MODKEY_CTRL_R,
    MODKEY_SHIFT, MODKEY_SHIFT_L, MODKEY_SHIFT_R,
)
from keyhac.core import log

logger = log.getLogger("WinHook")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # LRESULT / LONG_PTR: pointer-sized.  wintypes has no LRESULT; LPARAM is
    # the same underlying type.  Declaring every prototype below is mandatory
    # on 64-bit: ctypes defaults restype to c_int, which truncates handles
    # (a truncated HMODULE makes SetWindowsHookExW fail with 126).
    LRESULT = wintypes.LPARAM
    ULONG_PTR = ctypes.c_size_t

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105

    # Mouse messages that cancel a pending one-shot (button downs + wheels;
    # plain movement deliberately does not - keyhac-win behavior)
    WM_LBUTTONDOWN = 0x0201
    WM_RBUTTONDOWN = 0x0204
    WM_MBUTTONDOWN = 0x0207
    WM_XBUTTONDOWN = 0x020B
    WM_MOUSEWHEEL = 0x020A
    WM_MOUSEHWHEEL = 0x020E
    MOUSE_WHEEL_MSGS = frozenset([WM_MOUSEWHEEL, WM_MOUSEHWHEEL])
    MOUSE_CANCEL_MSGS = frozenset([
        WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN,
    ]) | MOUSE_WHEEL_MSGS

    LLKHF_EXTENDED = 0x01
    LLKHF_INJECTED = 0x10
    LLKHF_UP = 0x80

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC = 0

    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    #: ToUnicodeEx flag (Windows 10 1607+): translate without disturbing the
    #: keyboard's dead-key state. Older builds ignore it, which is why the
    #: call below is still made twice on a dead key.
    TOUNICODE_NO_STATE = 1 << 2

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x1000
    WHEEL_DELTA = 120
    MOUSEEVENTF_BUTTON = {
        ("left", True): 0x0002, ("left", False): 0x0004,
        ("right", True): 0x0008, ("right", False): 0x0010,
        ("middle", True): 0x0020, ("middle", False): 0x0040,
    }
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

    # dwExtraInfo signatures to classify our own injected events
    EXTRA_INFO_OWN = 0x4B484301    # "KHC" 0x01
    EXTRA_INFO_REPLAY = 0x4B484302  # "KHC" 0x02

    # Keys that need KEYEVENTF_EXTENDEDKEY when injected
    EXTENDED_VKS = frozenset([
        0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # nav cluster + arrows
        0x2C, 0x2D, 0x2E,                                # PrtScr, Ins, Del
        0x5B, 0x5C, 0x5D,                                # LWin, RWin, Apps
        0x90,                                            # NumLock
        0xA1, 0xA3, 0xA5,                                # RShift*, RCtrl, RAlt
        0x6F,                                            # numpad Divide
    ])
    # (*RShift is technically not extended, but keyhac-win's behavior is
    #  reproduced first and tuned during the Windows bring-up.)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    # lParam stays an integer here and is cast in the callback, so the same
    # value can be handed straight back to CallNextHookEx.
    LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class KBDLLMOUSESTRUCT(ctypes.Structure):  # MSLLHOOKSTRUCT
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT),
                    ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = wintypes.HKL
    user32.ToUnicodeEx.argtypes = [
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte * 256),
        ctypes.c_wchar_p, ctypes.c_int, wintypes.UINT, wintypes.HKL]
    user32.ToUnicodeEx.restype = ctypes.c_int
    user32.GetKeyboardType.argtypes = [ctypes.c_int]
    user32.GetKeyboardType.restype = ctypes.c_int
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class WinInputHook(InputHook):

    SANITY_CHECK_STRIKES = 4  # from keyhac-win: 4 changes without callbacks

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinInputHook requires Windows")

        self._on_key: Callable[[KeyEvent], bool] | None = None
        self._on_restored: Callable[[], None] | None = None
        self._on_mouse: Callable[[], None] | None = None
        self._hook_handle = None
        self._hook_proc_ref = None      # must outlive the hook
        self._mouse_hook_handle = None
        self._mouse_proc_ref = None     # must outlive the hook
        self._callback_seen = False     # reset by sanity check
        self._sanity_state = None
        self._sanity_count = 0
        self._modifier_vks = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)

    # ------------------------------------------------------------------

    def install(self, on_key, on_restored, on_mouse=None) -> None:
        if self._hook_handle is not None:
            logger.warning("Keyboard hook is already installed.")
            return

        self._on_key = on_key
        self._on_restored = on_restored
        self._on_mouse = on_mouse
        self._hook_proc_ref = LowLevelKeyboardProc(self._hook_proc)

        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc_ref,
            kernel32.GetModuleHandleW(None), 0)
        if not self._hook_handle:
            error = ctypes.get_last_error()
            self._hook_handle = None
            raise RuntimeError(
                f"SetWindowsHookExW failed: {error} ({ctypes.FormatError(error)})")

        if on_mouse is not None:
            # Observation-only WH_MOUSE_LL for one-shot cancellation. Failing
            # to install it degrades that one feature, not the app - warn and
            # continue rather than tearing the keyboard hook down.
            self._mouse_proc_ref = LowLevelKeyboardProc(self._mouse_hook_proc)
            self._mouse_hook_handle = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_proc_ref,
                kernel32.GetModuleHandleW(None), 0)
            if not self._mouse_hook_handle:
                error = ctypes.get_last_error()
                self._mouse_proc_ref = None
                logger.warning(
                    f"WH_MOUSE_LL install failed: {error} "
                    f"({ctypes.FormatError(error)}); one-shot modifiers will "
                    "not cancel on mouse input.")

        logger.info("Keyboard hook installed.")

    def uninstall(self) -> None:
        if self._mouse_hook_handle is not None:
            user32.UnhookWindowsHookEx(self._mouse_hook_handle)
            self._mouse_hook_handle = None
            self._mouse_proc_ref = None
        if self._hook_handle is None:
            return
        user32.UnhookWindowsHookEx(self._hook_handle)
        self._hook_handle = None
        self._hook_proc_ref = None
        logger.info("Keyboard hook uninstalled.")

    @property
    def installed(self) -> bool:
        return self._hook_handle is not None

    # ------------------------------------------------------------------

    def _hook_proc(self, n_code, w_param, l_param):
        if n_code < 0:
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._callback_seen = True
        kbd = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = int(kbd.vkCode)
        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)

        extra = int(kbd.dwExtraInfo)
        if extra == EXTRA_INFO_OWN:
            # Our own translated output - never re-processed
            return user32.CallNextHookEx(None, n_code, w_param, l_param)
        kind = "replay" if extra == EXTRA_INFO_REPLAY else "real"

        try:
            consumed = self._on_key(KeyEvent(vk, down, kind)) if self._on_key else False
        except Exception:
            logger.error("Key handler raised; passing event through.")
            consumed = False

        if consumed:
            return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _mouse_hook_proc(self, n_code, w_param, l_param):
        """WH_MOUSE_LL: observation only, never consumes. Physical button
        downs and wheel turns cancel a pending one-shot modifier; our own
        injected mouse output (sentinel dwExtraInfo) is ignored."""
        if n_code >= 0 and w_param in MOUSE_CANCEL_MSGS:
            mouse = ctypes.cast(
                l_param, ctypes.POINTER(KBDLLMOUSESTRUCT)).contents
            if int(mouse.dwExtraInfo) not in (EXTRA_INFO_OWN, EXTRA_INFO_REPLAY):
                try:
                    if self._on_mouse is not None:
                        self._on_mouse(
                            "wheel" if w_param in MOUSE_WHEEL_MSGS else "button")
                except Exception:
                    logger.error("Mouse handler raised; event passed through.")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    # ------------------------------------------------------------------

    def send(self, events: Sequence[tuple[int, bool]], replay: bool = False) -> None:
        if not events:
            return
        extra = EXTRA_INFO_REPLAY if replay else EXTRA_INFO_OWN
        inputs = (INPUT * len(events))()
        for i, (vk, down) in enumerate(events):
            flags = 0 if down else KEYEVENTF_KEYUP
            if vk in EXTENDED_VKS:
                flags |= KEYEVENTF_EXTENDEDKEY
            scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
            inputs[i].type = INPUT_KEYBOARD
            inputs[i].union.ki = KEYBDINPUT(vk, scan, flags, 0, extra)
        sent = user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))
        if sent != len(events):
            error = ctypes.get_last_error()
            logger.error(f"SendInput sent {sent}/{len(events)} events "
                         f"(error {error}: {ctypes.FormatError(error)})")

    # ------------------------------------------------------------------

    def check_health(self) -> None:
        """Called from a ~100 ms timer.  Port of keyhac-win checkSanity():
        if the async modifier key state keeps changing while our hook sees no
        callbacks, Windows silently unhooked us - re-install."""
        state = tuple(user32.GetAsyncKeyState(vk) & 0x8000 for vk in self._modifier_vks)

        if self._callback_seen:
            self._callback_seen = False
            self._sanity_count = 0
            self._sanity_state = state
            return

        if state != self._sanity_state:
            self._sanity_state = state
            self._sanity_count += 1
            if self._sanity_count >= WinInputHook.SANITY_CHECK_STRIKES:
                logger.warning("Key hook force cancellation detected - re-installing.")
                self._sanity_count = 0
                on_key, on_restored = self._on_key, self._on_restored
                on_mouse = self._on_mouse
                self.uninstall()
                self.install(on_key, on_restored, on_mouse)
                if on_restored is not None:
                    on_restored()

    def keyboard_layout(self) -> str:
        # GetKeyboardType(0) == 7 means a Japanese keyboard (keyhac-win rule)
        return "jis" if user32.GetKeyboardType(0) == 7 else "ansi"

    # ------------------------------------------------------------------

    def char_for_key(self, vk: int, mod: int = 0) -> str | None:
        """The character `vk` produces on the active layout (InputHook API).

        ``ToUnicodeEx`` against the foreground thread's keyboard layout: the
        same translation Windows performs for a real keystroke, so the answer
        follows whatever layout is selected with no table of our own to go
        stale. AltGr is Ctrl+Alt on Windows, which is why an Alt in `mod`
        sets both - several layouts need it for ``@`` and the backslash.

        lazydocs: ignore
        """
        if mod & (MODKEY_CMD | MODKEY_CMD_L | MODKEY_CMD_R):
            return None
        alt = bool(mod & (MODKEY_ALT | MODKEY_ALT_L | MODKEY_ALT_R))
        if mod & (MODKEY_CTRL | MODKEY_CTRL_L | MODKEY_CTRL_R) and not alt:
            # A Ctrl chord is a command, not text. Ctrl+Alt is AltGr.
            return None

        state = (ctypes.c_ubyte * 256)()
        if mod & (MODKEY_SHIFT | MODKEY_SHIFT_L | MODKEY_SHIFT_R):
            state[VK_SHIFT] = 0x80
        if alt:
            state[VK_CONTROL] = 0x80
            state[VK_MENU] = 0x80

        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        layout = user32.GetKeyboardLayout(0)
        buffer = ctypes.create_unicode_buffer(8)
        count = user32.ToUnicodeEx(vk, scan, ctypes.byref(state), buffer,
                                   len(buffer), TOUNICODE_NO_STATE, layout)
        if count < 0:
            # A dead key. The flag above leaves the state alone on Windows 10
            # 1607+; on anything older the state is now armed, so translate
            # again to consume it rather than leaving the next real keystroke
            # to be composed against it.
            user32.ToUnicodeEx(vk, scan, ctypes.byref(state), buffer,
                               len(buffer), TOUNICODE_NO_STATE, layout)
            return None
        if count != 1:
            return None
        text = buffer[0]
        return text if text.isprintable() else None

    def cursor_pos(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (int(pt.x), int(pt.y))

    def send_mouse(self, events, replay: bool = False) -> None:
        """Inject mouse events (see InputHook.send_mouse for the item
        vocabulary). Moves are converted from relative pixels to an absolute
        virtual-desktop position: MOUSEEVENTF_MOVE alone is subject to
        pointer acceleration, which would distort the requested distance
        (the reason keyhac-win's MouseMoveCommand computed absolute
        coordinates too)."""
        if not events:
            return
        extra = EXTRA_INFO_REPLAY if replay else EXTRA_INFO_OWN
        cur_x = cur_y = None
        inputs = (INPUT * len(events))()
        for i, event in enumerate(events):
            kind = event[0]
            dx = dy = 0
            data = 0
            if kind == "move":
                if cur_x is None:
                    cur_x, cur_y = self.cursor_pos()
                cur_x += int(event[1])
                cur_y += int(event[2])
                vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
                vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
                vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
                vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
                dx = round((cur_x - vx) * 65535 / max(1, vw - 1))
                dy = round((cur_y - vy) * 65535 / max(1, vh - 1))
                flags = (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                         | MOUSEEVENTF_VIRTUALDESK)
            elif kind in ("wheel", "hwheel"):
                data = int(event[1] * WHEEL_DELTA) & 0xFFFFFFFF
                flags = (MOUSEEVENTF_WHEEL if kind == "wheel"
                         else MOUSEEVENTF_HWHEEL)
            else:
                try:
                    flags = MOUSEEVENTF_BUTTON[(kind, bool(event[1]))]
                except KeyError:
                    raise ValueError(f"Unknown mouse event: {event!r}") from None
            inputs[i].type = INPUT_MOUSE
            inputs[i].union.mi = MOUSEINPUT(dx, dy, data, flags, 0, extra)
        sent = user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))
        if sent != len(events):
            error = ctypes.get_last_error()
            logger.error(f"SendInput sent {sent}/{len(events)} mouse events "
                         f"(error {error}: {ctypes.FormatError(error)})")

    KEYEVENTF_UNICODE = 0x0004

    def send_text(self, s: str) -> None:
        """Type a literal string via KEYEVENTF_UNICODE (one down+up pair per
        UTF-16 code unit; surrogate pairs arrive as two units, which is the
        documented way to inject non-BMP characters)."""
        units = s.encode("utf-16-le")
        n = len(units) // 2
        if n == 0:
            return
        inputs = (INPUT * (n * 2))()
        for i in range(n):
            code = int.from_bytes(units[i * 2:i * 2 + 2], "little")
            for j, flags in ((0, WinInputHook.KEYEVENTF_UNICODE),
                             (1, WinInputHook.KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                inputs[i * 2 + j].type = INPUT_KEYBOARD
                inputs[i * 2 + j].union.ki = KEYBDINPUT(
                    0, code, flags, 0, EXTRA_INFO_OWN)
        sent = user32.SendInput(n * 2, inputs, ctypes.sizeof(INPUT))
        if sent != n * 2:
            logger.error(f"send_text sent {sent}/{n * 2} events")
