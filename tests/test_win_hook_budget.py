"""The hook callback's budget, and the only warning there is that it was spent.

Windows removes a low-level hook whose callback overruns LowLevelHooksTimeout
and hands the event that overran to the application anyway - "there is no way
for the application to know whether the hook is removed", says the
LowLevelKeyboardProc documentation. The sanity check recovers afterwards; this
warning is the evidence that a key which leaked was a key that overran.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

import logging  # noqa: E402
import time  # noqa: E402

from keyhac.platform.win.hook import WinInputHook  # noqa: E402


class _Kbd:
    """The KBDLLHOOKSTRUCT fields _hook_proc reads, as a plain object; the
    proc is driven directly rather than through SetWindowsHookEx, which only
    the OS can call."""

    def __init__(self, vk):
        self.vkCode = vk
        self.dwExtraInfo = 0


def _drive(monkeypatch, hook, on_key):
    """Call _hook_proc once for a key-down of 'A', with the ctypes cast and
    CallNextHookEx stubbed out."""
    from keyhac.platform.win import hook as hook_module

    monkeypatch.setattr(hook_module.ctypes, "cast",
                        lambda *_args: type("_P", (), {"contents": _Kbd(0x41)})())
    monkeypatch.setattr(hook_module.user32, "CallNextHookEx",
                        lambda *_args: 0)
    hook._on_key = on_key
    return hook._hook_proc(0, 0x0100, 0)  # HC_ACTION, WM_KEYDOWN


def test_a_slow_callback_is_warned_about(monkeypatch, caplog):
    hook = WinInputHook()
    monkeypatch.setattr(WinInputHook, "SLOW_CALLBACK_SECONDS", 0.02)
    with caplog.at_level(logging.WARNING):
        consumed = _drive(monkeypatch, hook,
                          lambda event: time.sleep(0.05) or True)
    assert consumed == 1, "the decision still stands as far as we are concerned"
    assert any("past the hook's budget" in record.message
               for record in caplog.records)


def test_a_quick_callback_says_nothing(monkeypatch, caplog):
    hook = WinInputHook()
    with caplog.at_level(logging.WARNING):
        _drive(monkeypatch, hook, lambda event: True)
    assert not [record for record in caplog.records
                if "budget" in record.message]
