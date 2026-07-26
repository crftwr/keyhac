"""Windows keyboard hook - WH_KEYBOARD_LL + SendInput via ctypes.

Reimplements the behavior keyhac-win got from pyauto (pyautocore.pyd):
- low-level keyboard hook installed on the main thread; callbacks are
  delivered while that thread pumps messages; return nonzero to consume
- injection via SendInput() batches, tagged through dwExtraInfo so the hook
  can classify its own events ("own" filtered, "replay" re-processed)
- silent-unhook recovery: Windows removes a hook whose callback exceeds
  LowLevelHooksTimeout (~300 ms) without any notification; a periodic sanity
  check (ported from keyhac-win Keymap.checkSanity) detects modifier-state
  changes that arrived without hook callbacks and re-installs the hook

STATUS: written to spec, NOT yet run on Windows (M1 was developed on macOS).
The first Windows session must run tools/hook_echo.py before anything else.
"""

import ctypes
import sys
from typing import Callable, Sequence

from keyhac.platform.base import InputHook, KeyEvent
from keyhac.core import log

logger = log.getLogger("WinHook")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105

    LLKHF_EXTENDED = 0x01
    LLKHF_INJECTED = 0x10
    LLKHF_UP = 0x80

    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC = 0

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
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT))

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


class WinInputHook(InputHook):

    SANITY_CHECK_STRIKES = 4  # from keyhac-win: 4 changes without callbacks

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WinInputHook requires Windows")

        self._on_key: Callable[[KeyEvent], bool] | None = None
        self._on_restored: Callable[[], None] | None = None
        self._hook_handle = None
        self._hook_proc_ref = None      # must outlive the hook
        self._callback_seen = False     # reset by sanity check
        self._sanity_state = None
        self._sanity_count = 0
        self._modifier_vks = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)

    # ------------------------------------------------------------------

    def install(self, on_key, on_restored) -> None:
        if self._hook_handle is not None:
            logger.warning("Keyboard hook is already installed.")
            return

        self._on_key = on_key
        self._on_restored = on_restored
        self._hook_proc_ref = LowLevelKeyboardProc(self._hook_proc)

        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc_ref,
            kernel32.GetModuleHandleW(None), 0)
        if not self._hook_handle:
            raise RuntimeError(f"SetWindowsHookExW failed: {kernel32.GetLastError()}")

        logger.info("Keyboard hook installed.")

    def uninstall(self) -> None:
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
        kbd = l_param.contents
        vk = int(kbd.vkCode)
        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)

        extra = ctypes.cast(kbd.dwExtraInfo, ctypes.c_void_p).value or 0
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
            logger.error(f"SendInput sent {sent}/{len(events)} events "
                         f"(error {kernel32.GetLastError()})")

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
                self.uninstall()
                self.install(on_key, on_restored)
                if on_restored is not None:
                    on_restored()

    def keyboard_layout(self) -> str:
        # GetKeyboardType(0) == 7 means a Japanese keyboard (keyhac-win rule)
        return "jis" if user32.GetKeyboardType(0) == 7 else "ansi"
