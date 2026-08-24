"""Windows IME control - IMM32 through the focused window's IME window.

The IME open/close state lives in an input context, which by default belongs
to a thread and is shared by that thread's windows.  A context in another
process is not reachable through ImmGetOpenStatus (the HIMC is process-local),
so the way in is the one pyauto used: ask the target thread's hidden IME
window, which ImmGetDefaultIMEWnd() hands back, over WM_IME_CONTROL.

Two things make that message send worth care:

- It is a *cross-process synchronous* send, and this runs on the main thread,
  i.e. inside the WH_KEYBOARD_LL callback when a key action triggers it.  A
  plain SendMessage into a hung or non-pumping target would stall the hook past
  LowLevelHooksTimeout (300 ms by default) and Windows would silently unhook
  it.  Hence SendMessageTimeout with a short cap and SMTO_ABORTIFHUNG; the
  SMTO_NORMAL half lets this thread keep servicing incoming sends meanwhile,
  so a target that is itself waiting on us cannot deadlock.
- IMM32 is answered by IMEs that keep an IMM32 compatibility surface, which
  Microsoft IME does.  A TSF-only IME may not answer at all - that is what
  get_status() returning None means, and why it is not folded into False.

Live-verified on Windows 11 with Microsoft IME (Japanese), 2026-08-23 - see
doc/dev/testing.md for what the pass measured, including the two things it
corrected here: the *focused* window, not the foreground one, is what
ImmGetDefaultIMEWnd has to be asked about, and an open status read under a
non-IME layout is a flag nobody consumes.
"""

import ctypes
import sys

from keyhac.core import log
from keyhac.platform.base import ImeProvider

logger = log.getLogger("WinIme")

#: Cap on the cross-process send.  Well under LowLevelHooksTimeout's 300 ms
#: default, since the whole budget is spent inside the hook callback.
SEND_TIMEOUT_MS = 100

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)

    # wintypes has no LRESULT/DWORD_PTR; both are pointer-sized, and LPARAM
    # already is (same reasoning as hook.py).  Mandatory on 64-bit: the
    # default c_int restype would truncate the HWNDs below.
    LRESULT = wintypes.LPARAM
    DWORD_PTR = wintypes.WPARAM

    WM_IME_CONTROL = 0x0283
    IMC_GETOPENSTATUS = 0x0005
    IMC_SETOPENSTATUS = 0x0006

    SMTO_NORMAL = 0x0000
    SMTO_ABORTIFHUNG = 0x0002

    #: ImmGetProperty index for the conversion modes a layout can offer.  Zero
    #: for a layout with no IME behind it - see _layout_has_ime().
    IGP_CONVERSION = 0x0008

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT)]

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD,
                                        ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(DWORD_PTR)]
    user32.SendMessageTimeoutW.restype = LRESULT
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = wintypes.HKL
    imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
    imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
    imm32.ImmGetProperty.argtypes = [wintypes.HKL, wintypes.DWORD]
    imm32.ImmGetProperty.restype = wintypes.UINT


class WinImeProvider(ImeProvider):
    """IME on/off of the input context the focused window types through."""

    def get_status(self) -> bool | None:
        hwnd = self._input_window()
        if not hwnd:
            logger.debug("No foreground window to reach an IME through.")
            return None
        if not self._layout_has_ime(hwnd):
            return False
        result = self._ime_control(hwnd, IMC_GETOPENSTATUS, 0)
        return None if result is None else bool(result)

    def set_status(self, on: bool) -> bool:
        hwnd = self._input_window()
        if not hwnd:
            logger.debug("No foreground window to reach an IME through.")
            return False
        if not self._layout_has_ime(hwnd):
            # Off is already true and stays true; on is unreachable.  The
            # open flag *would* take here - IMM32 stores it on the input
            # context whatever the layout - but nothing consumes it and the
            # next layout switch drops it (measured), so reporting success
            # would be a lie the caller cannot see through.
            return not on
        if self._ime_control(hwnd, IMC_SETOPENSTATUS, 1 if on else 0) is None:
            return False
        # Report what was actually reached: an IME may decline the change
        # (no conversion mode available for the current input language).
        return self.get_status() is on

    # ------------------------------------------------------------------

    @staticmethod
    def _input_window():
        """The window whose IME state is the one the user is typing into.

        Not the foreground window: a frame and the control that actually has
        the focus can resolve to *different* default IME windows, and only the
        focused one carries the live state.  Measured on Windows 11 with
        Notepad, whose RichEditD2DPT edit control answers open=0/1 in step with
        the IME while its top-level frame stays stuck at 0 - so asking the
        frame reads a phantom flag that nothing consumes and writing to it
        changes nothing the user can see.  Falls back to the foreground window
        when the thread reports no focus (a window can be active with focus
        nowhere).
        """
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
            return info.hwndFocus
        return hwnd

    @staticmethod
    def _layout_has_ime(hwnd) -> bool:
        """Whether the layout the focused window types under has an IME at all.

        Without this the answers are about a flag rather than about the IME:
        IMM32 keeps an open status on the input context even while a plain
        layout (en-US) is selected, so set_status(True) there reports success,
        composes nothing, and is gone at the next layout switch - all three
        measured on Windows 11 with Microsoft IME installed.

        The obvious test, ImmIsIME(), does not work: on that machine it answers
        true for the *US* layout too (false only for a layout that is not
        installed at all), so it separates nothing.  ImmGetDescription() and
        ImmGetIMEFileName() are no help either - both are empty even for
        Microsoft IME, which is a TSF text service with no IMM32 IME file
        behind it.  What does separate them is the set of conversion modes the
        layout offers: 0 for en-US, IME_CMODE_NATIVE|KATAKANA|FULLSHAPE (0xb)
        for Microsoft IME.
        """
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        hkl = user32.GetKeyboardLayout(tid)
        return bool(imm32.ImmGetProperty(hkl, IGP_CONVERSION))

    def _ime_control(self, hwnd, command: int, value: int) -> int | None:
        """Send one WM_IME_CONTROL to the given window's IME window.

        Returns the message result, or None when there is nothing to ask -
        no IME window for the target's thread, or the send timed out.
        """
        ime_hwnd = imm32.ImmGetDefaultIMEWnd(hwnd)
        if not ime_hwnd:
            logger.debug("The focused window's thread has no IME window.")
            return None

        result = DWORD_PTR()
        sent = user32.SendMessageTimeoutW(
            ime_hwnd, WM_IME_CONTROL, command, value,
            SMTO_NORMAL | SMTO_ABORTIFHUNG, SEND_TIMEOUT_MS,
            ctypes.byref(result))
        if not sent:
            logger.debug(f"WM_IME_CONTROL {command:#x} got no answer within "
                         f"{SEND_TIMEOUT_MS} ms (error "
                         f"{ctypes.get_last_error()}).")
            return None
        return result.value
