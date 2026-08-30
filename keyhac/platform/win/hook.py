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
import time
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

    # Integrity levels, for "is the window in front one we can even see keys
    # for" - see WinInputHook._foreground_is_out_of_reach.
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.c_void_p
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.c_void_p

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenIntegrityLevel = 25


def _hex(level) -> str:
    """An integrity level as it is written, or "unreadable"."""
    return "unreadable" if level is None else f"0x{level:04X}"


def _integrity_level(pid: int | None) -> int | None:
    """A process's integrity level as its SID's last sub-authority, or None.

    `None` for our own process. 0x2000 is medium (an ordinary application),
    0x3000 high (elevated); the numbers are compared, never named, because
    what matters is only whether one is above another.

    None when it cannot be read at all, which is an answer this is asked to
    make a decision *against*: the caller treats "cannot tell" as "in reach",
    so an unreadable process can never talk it out of a recovery.
    """
    try:
        if pid is None:
            handle, opened = kernel32.GetCurrentProcess(), False
        else:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, pid)
            opened = bool(handle)
            if not handle:
                return None
        token = wintypes.HANDLE()
        try:
            if not advapi32.OpenProcessToken(handle, TOKEN_QUERY,
                                             ctypes.byref(token)):
                return None
            try:
                size = wintypes.DWORD()
                advapi32.GetTokenInformation(token, TokenIntegrityLevel, None,
                                             0, ctypes.byref(size))
                if not size.value:
                    return None
                buffer = ctypes.create_string_buffer(size.value)
                if not advapi32.GetTokenInformation(token, TokenIntegrityLevel,
                                                    buffer, size.value,
                                                    ctypes.byref(size)):
                    return None
                # TOKEN_MANDATORY_LABEL is a SID_AND_ATTRIBUTES: the SID
                # pointer first, and the level is its last sub-authority.
                sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
                count = ctypes.cast(advapi32.GetSidSubAuthorityCount(sid),
                                    ctypes.POINTER(ctypes.c_ubyte))[0]
                return ctypes.cast(advapi32.GetSidSubAuthority(sid, count - 1),
                                   ctypes.POINTER(wintypes.DWORD))[0]
            finally:
                kernel32.CloseHandle(token)
        finally:
            if opened:
                kernel32.CloseHandle(handle)
    except Exception:
        logger.debug("Could not read an integrity level.", exc_info=True)
        return None


class WinInputHook(InputHook):

    SANITY_CHECK_STRIKES = 4  # from keyhac-win: 4 changes without callbacks

    #: A callback slower than this is logged, because nothing else will say
    #: so. Windows removes a low-level hook whose callback overruns
    #: LowLevelHooksTimeout (HKCU\Control Panel\Desktop, capped at 1000 ms
    #: since Windows 10 1709) and hands the event that overran to the
    #: application regardless of what we would have returned - and, in the
    #: documentation's own words, "there is no way for the application to know
    #: whether the hook is removed". The sanity check above recovers from that
    #: afterwards; this is the only warning available *before* it, and the
    #: only evidence afterwards that a key that leaked was a key that overran.
    SLOW_CALLBACK_SECONDS = 0.2

    #: Sent before a Windows key is released on recovery, so the release does
    #: not read as a Win *tap* and open the Start menu: the menu opens when a
    #: Win down is followed by its up with nothing in between. 0xE8 is
    #: unassigned - Windows does nothing with it, no layout produces it - and
    #: it is the same masking key AutoHotkey uses, for the same reason.
    MASK_VK = 0xE8

    #: The two of `_modifier_vks` that a bare tap is a command in.
    WIN_VKS = (0x5B, 0x5C)

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
        self._own_integrity = None      # read once, on the first strike
        #: Physical keys we have been shown the down of - see _let_go_of.
        self._seen_down = set()
        self._out_of_reach_title = None  # said so about this window already

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

        # Which physical keys we have actually been shown the *down* of, so
        # an up can tell whether the OS is holding one we never saw pressed.
        orphan = False
        if down:
            self._seen_down.add(vk)
        else:
            orphan = vk not in self._seen_down
            self._seen_down.discard(vk)

        started = time.perf_counter()
        try:
            consumed = self._on_key(KeyEvent(vk, down, kind)) if self._on_key else False
        except Exception:
            logger.error("Key handler raised; passing event through.")
            consumed = False
        elapsed = time.perf_counter() - started
        if elapsed >= WinInputHook.SLOW_CALLBACK_SECONDS:
            logger.warning(
                f"Key handler took {elapsed * 1000:.0f} ms for vk {vk} - past "
                f"the hook's budget. Windows may have dropped the hook and "
                f"passed the key to the application; move slow work off the "
                f"callback (ThreadedAction, or call_on_main_thread).")

        if consumed:
            if orphan and vk in self._modifier_vks:
                self._let_go_of(vk)
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
            # Asked on *every* strike, not once the fourth has been reached:
            # by then the window that was keeping the keys from us may be gone,
            # and the evidence of blindness is four ticks old while the
            # foreground is read now. Four hundred milliseconds is long enough
            # to close Task Manager in.
            if self._foreground_is_out_of_reach():
                # Not a cancellation: the hook is installed and working, and
                # the keys are simply not ours to see.
                self._sanity_count = 0
                return
            self._sanity_count += 1
            if self._sanity_count >= WinInputHook.SANITY_CHECK_STRIKES:
                title, theirs, ours = self._foreground_reach()
                logger.warning(
                    f"Key hook force cancellation detected - re-installing. "
                    f"The window in front is {title!r}, integrity "
                    f"{_hex(theirs)} against our {_hex(ours)}.")
                self._sanity_count = 0
                on_key, on_restored = self._on_key, self._on_restored
                on_mouse = self._on_mouse
                self.uninstall()
                self.install(on_key, on_restored, on_mouse)
                self.release_stuck_modifiers()
                if on_restored is not None:
                    on_restored()

    def _foreground_is_out_of_reach(self) -> bool:
        """Whether the window in front is one whose keys we never see anyway.

        **A hook that is given nothing looks exactly like a hook that was
        taken away**, and the sanity check cannot tell them apart on its own:
        both are modifier state moving while no callback arrives. UIPI is the
        second cause. A low-level hook installed by a medium-integrity process
        is not called for input aimed at a *higher* integrity window, and
        Task Manager is that window - measured on the machine this was
        reported from: Keyhac at integrity 0x2000 and Task Manager at 0x3000,
        elevated. Every keystroke typed in there produced a strike, so the
        hook was torn down and rebuilt over and over while nothing was wrong
        with it, and each rebuild is a gap physical events flow through.

        Keyhac's bindings do not work in such a window, and cannot without
        running elevated - that is Windows protecting it, not a bug to fix.
        What is a bug is calling it a force cancellation.

        The token read costs a process open, and runs on a strike - which is
        to say only where something already looks wrong, and where the
        alternative is a needless re-install.
        """
        title, theirs, ours = self._foreground_reach()
        if theirs is None or ours is None or theirs <= ours:
            return False
        if self._out_of_reach_title != title:
            self._out_of_reach_title = title
            logger.info(
                f"{title!r} runs at integrity {_hex(theirs)} and we are "
                f"{_hex(ours)}: Windows does not show us its keys, so no "
                f"binding fires there. Not a hook failure - and not treated "
                f"as one.")
        return True

    def _foreground_reach(self) -> tuple:
        """(what is in front, its integrity level, ours).

        A title rather than a process name because it is the log a person
        reads: "Task Manager" says which window it was, and the level beside
        it says why nothing fired in it. `None` for a level that could not be
        read, which every caller treats as *in* reach - the hook being gone is
        the case that costs the user their keyboard, so "cannot tell" must
        never talk the recovery out of running.
        """
        if self._own_integrity is None:
            self._own_integrity = _integrity_level(None)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ("nothing at all", None, self._own_integrity)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buffer = ctypes.create_unicode_buffer(120)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return (buffer.value or f"pid {pid.value}",
                _integrity_level(pid.value), self._own_integrity)

    def _let_go_of(self, vk: int) -> None:
        """Undo, at the OS level, a modifier down we were never shown.

        **An up whose down we never saw belongs to somebody else.** Windows
        hides input aimed at a higher-integrity window from our hook, so the
        *down* went to the OS while we were blind; if the up is then consumed
        - and a user modifier's up always is, it being a key no application
        may see - nothing is left to tell Windows the key came back up, and it
        holds it forever.

        Reported with LWin as User0 and Task Manager in front, which is the
        shortest road to it there is: pressing the Windows key opens the Start
        menu, the Start menu is *not* elevated, so the up arrives at a hook
        that never saw the down. The key stayed held afterwards, in every
        application, until it was pressed and released again.

        The physical up stays consumed - passing it through would hand the
        application a bare up, and for a user modifier that is exactly the
        event the whole feature promises never to send. An injected one is
        sent instead, masked for the same reason
        `release_stuck_modifiers()` masks: a Win down with its up and nothing
        in between is the Start menu's own shortcut.
        """
        logger.debug(f"Releasing 0x{vk:02X}: its up reached us, its down "
                     f"never did, so Windows is holding a key nobody pressed.")
        events = []
        if vk in WinInputHook.WIN_VKS:
            events += [(WinInputHook.MASK_VK, True), (WinInputHook.MASK_VK, False)]
        self.send(events + [(vk, False)])

    def release_stuck_modifiers(self) -> None:
        """Let go of the modifiers Windows still believes are held.

        **The gap is not symmetrical.** While the hook is gone the physical
        events go straight to the OS; when it comes back, the ones the config
        swallows never arrive there. A user modifier is never emitted, and a
        replaced key is emitted as something else, so a *down* Windows
        received during the gap is matched by an up it will never see.
        Reported with LWin retired to User0, which is the recommended way to
        get a spare modifier on Windows: after a force-cancellation the Start
        menu's modifier stays armed, every letter afterwards is a Win chord,
        and the only cure the user has is to press and release the key again
        - having first worked out which key it was.

        So every modifier the OS reports as down is released here, as our own
        injected event. A key the user is genuinely still holding is released
        with them, and that is the right trade: this runs only where events
        have already been missed, and a modifier that has to be pressed again
        is a great deal better than one nobody can find.

        `MASK_VK` goes first when a Windows key is among them, or the release
        would complete a Win tap and open the Start menu.

        lazydocs: ignore
        """
        held = [vk for vk in self._modifier_vks
                if user32.GetAsyncKeyState(vk) & 0x8000]
        if not held:
            return
        logger.warning("Releasing modifiers Windows still holds down: "
                       + ", ".join(f"0x{vk:02X}" for vk in held) + ".")
        events = []
        if any(vk in WinInputHook.WIN_VKS for vk in held):
            events += [(WinInputHook.MASK_VK, True), (WinInputHook.MASK_VK, False)]
        events += [(vk, False) for vk in held]
        self.send(events)

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
        documented way to inject non-BMP characters).

        ``surrogatepass`` because the string can carry a lone surrogate: text
        read back from a UTF-16 buffer that something cut between the halves
        of a pair decodes to one, and refusing to type it would raise from the
        middle of an action rather than type the one broken character."""
        units = s.encode("utf-16-le", "surrogatepass")
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
