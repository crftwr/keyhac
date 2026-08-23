"""Windows IME control - IMM32 through the foreground window's IME window.

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

STATUS: written to spec; needs a live Windows pass with Microsoft IME (see
doc/dev/testing.md).
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

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(DWORD_PTR)]
    user32.SendMessageTimeoutW.restype = LRESULT
    imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
    imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND


class WinImeProvider(ImeProvider):
    """IME on/off of the foreground window's input context."""

    def get_status(self) -> bool | None:
        result = self._ime_control(IMC_GETOPENSTATUS, 0)
        return None if result is None else bool(result)

    def set_status(self, on: bool) -> bool:
        if self._ime_control(IMC_SETOPENSTATUS, 1 if on else 0) is None:
            return False
        # Report what was actually reached: an IME may decline the change
        # (no conversion mode available for the current input language).
        return self.get_status() is on

    # ------------------------------------------------------------------

    def _ime_control(self, command: int, value: int) -> int | None:
        """Send one WM_IME_CONTROL to the foreground window's IME window.

        Returns the message result, or None when there is nothing to ask -
        no foreground window, no IME for its thread, or the send timed out.
        """
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            logger.debug("No foreground window to reach an IME through.")
            return None
        ime_hwnd = imm32.ImmGetDefaultIMEWnd(hwnd)
        if not ime_hwnd:
            logger.debug("The foreground window's thread has no IME window.")
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
