"""What the hook has to put back when Windows has been keeping keys from it.

Two ways that happens, and they leave the same wreckage. Windows removes a
hook whose callback overruns its budget and says nothing; and UIPI hides input
aimed at a higher-integrity window from a lower-integrity hook. Either way the
physical events reach the OS while we are blind, and the ones the config
swallows never reach it afterwards - so a *down* the OS received is matched by
an up it will never see, and it holds the key forever.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

import logging  # noqa: E402

from keyhac.platform.win.hook import WinInputHook  # noqa: E402

LSHIFT, LWIN = 0xA0, 0x5B


def _holding(monkeypatch, *vks):
    """Windows reporting exactly these keys as down."""
    from keyhac.platform.win import hook as hook_module

    monkeypatch.setattr(hook_module.user32, "GetAsyncKeyState",
                        lambda vk: 0x8000 if vk in vks else 0)


def _sent(monkeypatch, hook):
    """What the hook injects, as (vk, down) pairs."""
    events = []
    monkeypatch.setattr(hook, "send",
                        lambda pairs, replay=False: events.extend(pairs))
    return events


def test_a_windows_key_the_os_still_holds_is_released(monkeypatch, caplog):
    """The reported symptom: LWin retired to a user modifier, so its up is
    never emitted, and after a force-cancellation every letter is a Win
    chord."""
    hook = WinInputHook()
    _holding(monkeypatch, LWIN)
    events = _sent(monkeypatch, hook)
    with caplog.at_level(logging.WARNING):
        hook.release_stuck_modifiers()
    assert events[-1] == (LWIN, False)
    assert "0x5B" in caplog.text


def test_the_release_is_masked_so_the_start_menu_stays_shut(monkeypatch):
    """A Win down followed by its up with nothing in between *is* the Start
    menu's shortcut, and the down already happened."""
    hook = WinInputHook()
    _holding(monkeypatch, LWIN)
    events = _sent(monkeypatch, hook)
    hook.release_stuck_modifiers()
    assert events == [(WinInputHook.MASK_VK, True), (WinInputHook.MASK_VK, False),
                      (LWIN, False)]


def test_a_shift_needs_no_mask(monkeypatch):
    """Nothing opens on a bare Shift, and an injected key nobody asked for is
    an injected key that can go wrong."""
    hook = WinInputHook()
    _holding(monkeypatch, LSHIFT)
    events = _sent(monkeypatch, hook)
    hook.release_stuck_modifiers()
    assert events == [(LSHIFT, False)]


def test_nothing_held_is_nothing_sent(monkeypatch):
    hook = WinInputHook()
    _holding(monkeypatch)
    events = _sent(monkeypatch, hook)
    hook.release_stuck_modifiers()
    assert events == []


def test_the_force_cancellation_path_releases_before_it_reports(monkeypatch):
    """Order matters: the engine's on_restored resets the modifier state it
    knows about, and the OS's is what this puts back."""
    hook = WinInputHook()
    order = []
    monkeypatch.setattr(hook, "uninstall", lambda: order.append("uninstall"))
    monkeypatch.setattr(hook, "install",
                        lambda *a, **k: order.append("install"))
    monkeypatch.setattr(hook, "release_stuck_modifiers",
                        lambda: order.append("release"))
    # Whatever happens to be in front of the machine running the tests has no
    # business in this one - see the elevation test below.
    monkeypatch.setattr(hook, "_foreground_is_out_of_reach", lambda: False)
    hook._on_restored = lambda: order.append("on_restored")
    hook._sanity_count = WinInputHook.SANITY_CHECK_STRIKES - 1
    hook._callback_seen = False
    hook._sanity_state = None           # any state at all is a change
    _holding(monkeypatch, LWIN)
    hook.check_health()
    assert order == ["uninstall", "install", "release", "on_restored"]


def test_an_elevated_window_in_front_is_not_a_cancellation(monkeypatch):
    """A hook that is given nothing looks exactly like a hook that was taken
    away. UIPI is the second cause: a medium-integrity hook is not called for
    input aimed at a higher-integrity window, so every key typed in Task
    Manager was a strike, and the hook was torn down and rebuilt over and
    over while nothing was wrong with it - each rebuild a gap that physical
    events flow through."""
    hook = WinInputHook()
    done = []
    monkeypatch.setattr(hook, "uninstall", lambda: done.append("uninstall"))
    monkeypatch.setattr(hook, "install", lambda *a, **k: done.append("install"))
    monkeypatch.setattr(hook, "_foreground_is_out_of_reach", lambda: True)
    hook._on_restored = lambda: done.append("on_restored")
    hook._sanity_count = WinInputHook.SANITY_CHECK_STRIKES - 1
    hook._callback_seen = False
    hook._sanity_state = None
    _holding(monkeypatch)
    hook.check_health()
    assert done == [], "nothing was wrong with the hook"
    assert hook._sanity_count == 0, "and the strikes start again"


def test_a_window_we_cannot_read_is_taken_as_in_reach(monkeypatch):
    """"Cannot tell" must never talk the recovery out of running: the hook
    being gone is the case that costs the user their keyboard."""
    from keyhac.platform.win import hook as hook_module

    hook = WinInputHook()
    monkeypatch.setattr(hook_module, "_integrity_level", lambda pid: None)
    assert hook._foreground_is_out_of_reach() is False


def test_our_own_integrity_level_reads(monkeypatch):
    """The comparison is only as good as both halves of it."""
    from keyhac.platform.win.hook import _integrity_level

    ours = _integrity_level(None)
    assert isinstance(ours, int) and ours >= 0x1000


class _Kbd:
    """The KBDLLHOOKSTRUCT fields _hook_proc reads."""

    def __init__(self, vk, extra=0):
        self.vkCode = vk
        self.dwExtraInfo = extra


def _press(monkeypatch, hook, vk, down, consumed=True):
    """Drive _hook_proc once, with the ctypes cast and CallNextHookEx stubbed."""
    from keyhac.platform.win import hook as hook_module

    monkeypatch.setattr(hook_module.ctypes, "cast",
                        lambda *_a: type("_P", (), {"contents": _Kbd(vk)})())
    monkeypatch.setattr(hook_module.user32, "CallNextHookEx", lambda *_a: 0)
    hook._on_key = lambda event: consumed
    return hook._hook_proc(0, 0x0100 if down else 0x0101, 0)


def test_an_up_whose_down_we_never_saw_is_undone(monkeypatch):
    """The road the report came down: LWin as User0, Task Manager in front.
    Pressing the Windows key there opens the Start menu - which is not
    elevated - so the up arrives at a hook that never saw the down, is
    consumed as a user modifier's up always is, and Windows is left holding a
    key nobody is pressing."""
    hook = WinInputHook()
    events = _sent(monkeypatch, hook)
    assert _press(monkeypatch, hook, LWIN, down=False) == 1, "still consumed"
    assert events == [(WinInputHook.MASK_VK, True), (WinInputHook.MASK_VK, False),
                      (LWIN, False)]


def test_an_up_we_did_see_the_down_of_is_left_alone(monkeypatch):
    """The ordinary case, which is every keystroke: an injected up on top of
    a consumed one would be a modifier the application never asked to see."""
    hook = WinInputHook()
    events = _sent(monkeypatch, hook)
    _press(monkeypatch, hook, LWIN, down=True)
    _press(monkeypatch, hook, LWIN, down=False)
    assert events == []


def test_an_orphan_up_that_is_passed_through_needs_no_help(monkeypatch):
    """Not consumed means Windows is about to get the up itself."""
    hook = WinInputHook()
    events = _sent(monkeypatch, hook)
    assert _press(monkeypatch, hook, LWIN, down=False, consumed=False) == 0
    assert events == []


def test_only_modifiers_are_worth_undoing(monkeypatch):
    """A letter nobody released latches nothing; a modifier does."""
    hook = WinInputHook()
    events = _sent(monkeypatch, hook)
    _press(monkeypatch, hook, 0x41, down=False)      # 'A'
    assert events == []
