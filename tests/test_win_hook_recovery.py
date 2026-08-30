"""What a re-installed hook has to put back.

Windows removes a low-level hook whose callback overruns its budget, and the
sanity check re-installs it - but the gap it recovers from is not symmetrical.
While the hook was gone the physical events went straight to the OS; when it
comes back, the ones the config swallows never arrive there, so a *down* the
OS received during the gap is matched by an up it will never see.
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
    hook._on_restored = lambda: order.append("on_restored")
    hook._sanity_count = WinInputHook.SANITY_CHECK_STRIKES - 1
    hook._callback_seen = False
    hook._sanity_state = None           # any state at all is a change
    _holding(monkeypatch, LWIN)
    hook.check_health()
    assert order == ["uninstall", "install", "release", "on_restored"]
