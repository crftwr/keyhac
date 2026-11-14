"""macOS hook event-ordering machinery (keyhac/platform/mac/hook.py).

Real events arriving while injected events are in flight must be deferred and
re-posted once the batch drains (or after the 0.2 s watchdog) - the ordering
contract stated on InputHook.send in platform/base.py.  The state machine is
plain Python driven by _tap_callback, so no live event tap is needed: the
handful of Quartz.CGEvent* calls the hook makes are monkeypatched to operate
on fake event objects and the callback/timer are driven directly.  The
live-race counterpart of these tests is tools/hook_echo.py --stress-ordering.
"""

import sys
from types import SimpleNamespace

import pytest

from keyhac.platform.base import KeyEvent

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

TRANSLATED_ID = 1001
REPLAY_ID = 1002
HID_ID = 7  # matches neither private source -> classified "real"


class FakeEvent:
    def __init__(self, vk, source_id, event_type, flags=0):
        self.vk = vk
        self.source_id = source_id
        self.event_type = event_type
        self.flags = flags
        self.user_data = 0


class Harness:
    """MacInputHook with its Quartz calls faked; drives _tap_callback directly."""

    def __init__(self, monkeypatch):
        import Quartz
        from keyhac.platform.mac.hook import MacInputHook

        self.Q = Quartz
        self.posted = []   # every event passed to CGEventPost, in order
        self.keys = []     # every KeyEvent delivered to on_key
        self.consume = lambda event: False
        self.restored = 0

        def get_field(event, field):
            if field == Quartz.kCGKeyboardEventKeycode:
                return event.vk
            if field == Quartz.kCGEventSourceStateID:
                return event.source_id
            if field == Quartz.kCGEventSourceUserData:
                return event.user_data
            raise AssertionError(f"unexpected field {field}")

        def set_field(event, field, value):
            assert field == Quartz.kCGEventSourceUserData
            event.user_data = value

        def create_key_event(source, vk, down):
            event_type = Quartz.kCGEventKeyDown if down else Quartz.kCGEventKeyUp
            return FakeEvent(vk, source.state_id, event_type)

        monkeypatch.setattr(Quartz, "CGEventPost", lambda tap, e: self.posted.append(e))
        monkeypatch.setattr(Quartz, "CGEventGetIntegerValueField", get_field)
        monkeypatch.setattr(Quartz, "CGEventSetIntegerValueField", set_field)
        monkeypatch.setattr(Quartz, "CGEventGetFlags", lambda e: e.flags)
        monkeypatch.setattr(Quartz, "CGEventSetFlags",
                            lambda e, flags: setattr(e, "flags", flags))
        monkeypatch.setattr(Quartz, "CGEventSetType",
                            lambda e, event_type: setattr(e, "event_type", event_type))
        monkeypatch.setattr(Quartz, "CGEventCreateKeyboardEvent", create_key_event)

        self.hook = MacInputHook()
        self.hook._source_translated = SimpleNamespace(state_id=TRANSLATED_ID)
        self.hook._source_replay = SimpleNamespace(state_id=REPLAY_ID)
        self.hook._source_translated_id = TRANSLATED_ID
        self.hook._source_replay_id = REPLAY_ID
        self.hook._on_key = self._handle_key
        self.hook._on_restored = self._handle_restored

    def _handle_key(self, event):
        self.keys.append(event)
        return self.consume(event)

    def _handle_restored(self):
        self.restored += 1

    def real(self, vk=0x00, down=True):
        event_type = self.Q.kCGEventKeyDown if down else self.Q.kCGEventKeyUp
        return FakeEvent(vk, HID_ID, event_type)

    def deliver(self, event):
        return self.hook._tap_callback(None, event.event_type, event, None)

    def tick(self, n=1):
        for _ in range(n):
            self.hook._on_timer(None)


@pytest.fixture
def h(monkeypatch):
    return Harness(monkeypatch)


# -- classification -------------------------------------------------------

def test_real_event_passes_through_when_idle(h):
    event = h.real(0x00, True)
    assert h.deliver(event) is event
    assert h.keys == [KeyEvent(0x00, True, "real")]
    assert h.posted == []


def test_translated_events_skip_the_handler(h):
    h.hook.send([(0x04, True)])
    assert [e.source_id for e in h.posted] == [TRANSLATED_ID]
    event = h.posted[0]
    assert h.deliver(event) is event
    assert h.keys == []


def test_replay_events_reenter_the_handler(h):
    h.hook.send([(0x04, True)], replay=True)
    assert [e.source_id for e in h.posted] == [REPLAY_ID]
    h.deliver(h.posted[0])
    assert h.keys == [KeyEvent(0x04, True, "replay")]


def test_consumed_event_is_nulled(h):
    h.consume = lambda event: True
    event = h.real(0x00, True)
    h.deliver(event)
    assert event.event_type == h.Q.kCGEventNull


def test_handler_error_passes_event_through(h):
    def boom(event):
        raise RuntimeError("boom")
    h.consume = boom
    event = h.real(0x00, True)
    assert h.deliver(event) is event
    assert event.event_type == h.Q.kCGEventKeyDown  # not nulled


# -- ordering: defer real events while injected ones are in flight --------

def test_send_counts_pending_and_arms_watchdog(h):
    h.hook.send([(0x04, True), (0x04, False)])
    assert h.hook._num_pending_virtual == 2
    assert h.hook._flush_countdown == pytest.approx(0.2)


def test_real_deferred_while_virtual_in_flight(h):
    h.hook.send([(0x04, True), (0x04, False)])
    event = h.real(0x00, True)
    assert h.deliver(event) is None          # swallowed, not delivered onward
    assert h.keys == []                      # and not processed yet
    assert h.hook._deferred_real_events == [event]


def test_deferred_reals_flush_after_batch_drains_in_order(h):
    h.hook.send([(0x04, True), (0x04, False)])
    batch = list(h.posted)
    a = h.real(0x00, True)
    b = h.real(0x00, False)
    h.deliver(a)
    h.deliver(b)

    h.deliver(batch[0])                      # one virtual still in flight
    assert h.hook._deferred_real_events == [a, b]
    assert h.posted == batch

    h.deliver(batch[1])                      # batch drained -> flush
    assert h.hook._deferred_real_events == []
    assert h.posted[2:] == [a, b]            # re-posted behind the batch, in order

    h.deliver(a)                             # ...and processed on re-entry
    h.deliver(b)
    assert h.keys == [KeyEvent(0x00, True, "real"), KeyEvent(0x00, False, "real")]


def test_replay_batch_also_gates_deferral(h):
    h.consume = lambda event: event.kind == "replay"
    h.hook.send([(0x04, True)], replay=True)
    a = h.real(0x00, True)
    h.deliver(a)
    assert h.hook._deferred_real_events == [a]

    h.deliver(h.posted[0])
    assert h.hook._num_pending_virtual == 0
    assert h.posted[1:] == [a]


def test_watchdog_flushes_when_batch_never_returns(h):
    h.hook.send([(0x04, True)])
    a = h.real(0x00, True)
    h.deliver(a)

    h.tick(6)                                # ~0.1998 s elapsed - not yet
    assert h.hook._deferred_real_events == [a]

    h.tick(1)                                # crosses the 0.2 s timeout
    assert h.hook._deferred_real_events == []
    assert h.hook._num_pending_virtual == 0
    assert h.posted[1:] == [a]               # the keystroke is not lost
    assert h.hook._num_pending_reposts == 1  # and the re-post is guarded too


def test_fresh_real_defers_until_reposts_return(h):
    """The flush-window race found by tools/hook_echo.py --stress-ordering
    (inherited from keyhac-mac): a real event arriving while re-posted
    deferred reals are still in flight must not overtake them."""
    h.hook.send([(0x04, True)])
    a = h.real(0x00, True)
    h.deliver(a)                             # deferred behind the batch
    h.deliver(h.posted[0])                   # batch drains -> a re-posted

    c = h.real(0x01, True)
    assert h.deliver(c) is None              # gated: a's re-post is in flight
    assert h.hook._deferred_real_events == [c]

    h.deliver(a)                             # a returns -> c flushed behind it
    assert h.posted[2:] == [c]
    h.deliver(c)
    assert [e.vk for e in h.keys] == [0x00, 0x01]   # original physical order


def test_send_rearms_the_watchdog(h):
    h.hook.send([(0x04, True)])
    h.tick(4)
    h.hook.send([(0x04, False)])
    assert h.hook._flush_countdown == pytest.approx(0.2)
    h.tick(6)
    assert h.hook._num_pending_virtual == 2  # no flush: countdown was re-armed


def test_restore_resets_ordering_state(h):
    h.hook.send([(0x04, True)])
    a = h.real(0x00, True)
    h.deliver(a)
    posted_before = len(h.posted)

    disabled = FakeEvent(0, HID_ID, h.Q.kCGEventTapDisabledByTimeout)
    h.deliver(disabled)
    assert h.restored == 1
    assert h.hook._num_pending_virtual == 0
    assert h.hook._deferred_real_events == []    # dropped, not re-posted
    assert len(h.posted) == posted_before


# -- mouse events: one-shot cancel branch (tap-mask route) -----------------

def mouse_event(h, event_type, source_id=HID_ID):
    return FakeEvent(0, source_id, event_type)


def test_real_mouse_down_calls_on_mouse_and_passes(h):
    cancels = []
    h.hook._on_mouse = cancels.append
    event = mouse_event(h, h.Q.kCGEventLeftMouseDown)
    assert h.deliver(event) is event         # observation only, never consumed
    assert cancels == ["button"]
    assert h.keys == []                      # never enters the key path


def test_scroll_wheel_cancels_one_shot_too(h):
    """...and reports itself as a wheel, which is not interchangeable with a
    button press: an open chooser dismisses on the second, not the first."""
    cancels = []
    h.hook._on_mouse = cancels.append
    event = mouse_event(h, h.Q.kCGEventScrollWheel)
    assert h.deliver(event) is event
    assert cancels == ["wheel"]


def test_own_and_replay_mouse_output_do_not_cancel(h):
    cancels = []
    h.hook._on_mouse = cancels.append
    for source_id in (TRANSLATED_ID, REPLAY_ID):
        event = mouse_event(h, h.Q.kCGEventLeftMouseDown, source_id)
        assert h.deliver(event) is event
    assert cancels == []


def test_mouse_events_bypass_the_deferral_queue(h):
    # A physical click during the key-flush window must not be queued: the
    # deferral machinery orders keyboard events, and motion is not tapped,
    # so a re-posted click would land after moves that followed it.
    cancels = []
    h.hook._on_mouse = cancels.append
    h.hook.send([(0x04, True)])              # key batch in flight
    event = mouse_event(h, h.Q.kCGEventRightMouseDown)
    assert h.deliver(event) is event         # passed straight through
    assert h.hook._deferred_real_events == []
    assert cancels == ["button"]
    assert h.hook._num_pending_virtual == 1  # and the key ledger untouched


def test_mouse_handler_error_passes_event_through(h):
    def boom(kind):
        raise RuntimeError("boom")
    h.hook._on_mouse = boom
    event = mouse_event(h, h.Q.kCGEventLeftMouseDown)
    assert h.deliver(event) is event


def test_mouse_event_without_handler_is_ignored(h):
    event = mouse_event(h, h.Q.kCGEventOtherMouseDown)
    assert h.deliver(event) is event
    assert h.keys == []


# -- the ctypes entry point the tap actually calls ------------------------
#
# A PyObjC tap callback leaks a reference to every CGEvent it returns, so the
# tap calls _tap_entry with raw pointers instead. These use real CGEvents:
# the entry point's job is exactly the pointer <-> proxy conversion.

class _RawEvent:
    """A CGEvent known only by pointer - no PyObjC proxy exists for it, as
    with the event the tap hands over. Owns one retain until close()."""

    def __init__(self):
        import ctypes
        self._cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self._cg.CGEventCreateKeyboardEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
        self._cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        self._cg.CFGetRetainCount.argtypes = [ctypes.c_void_p]
        self._cg.CFGetRetainCount.restype = ctypes.c_long
        self._cg.CFRelease.argtypes = [ctypes.c_void_p]
        self.ptr = self._cg.CGEventCreateKeyboardEvent(None, 0x00, True)

    def retains(self):
        return self._cg.CFGetRetainCount(self.ptr)

    def close(self):
        self._cg.CFRelease(self.ptr)


def _cg_event_proxies():
    import gc
    gc.collect()
    return sum(1 for o in gc.get_objects() if type(o).__name__ == "CGEventRef")


def _bare_hook():
    from keyhac.platform.mac.hook import MacInputHook
    return MacInputHook()


def test_entry_returns_the_pointer_for_a_passed_event(monkeypatch):
    hook = _bare_hook()
    seen = []

    def callback(proxy, event_type, event, refcon):
        seen.append(event.__c_void_p__().value)
        return event
    monkeypatch.setattr(hook, "_tap_callback", callback)
    raw = _RawEvent()
    assert hook._tap_entry(None, 10, raw.ptr, None) == raw.ptr
    assert seen == [raw.ptr]
    raw.close()


def test_entry_returns_null_for_a_dropped_event(monkeypatch):
    hook = _bare_hook()
    monkeypatch.setattr(hook, "_tap_callback", lambda *a: None)
    raw = _RawEvent()
    assert hook._tap_entry(None, 10, raw.ptr, None) is None
    raw.close()


def test_entry_passes_the_event_through_when_the_callback_raises(monkeypatch):
    hook = _bare_hook()

    def boom(*args):
        raise RuntimeError("boom")
    monkeypatch.setattr(hook, "_tap_callback", boom)
    raw = _RawEvent()
    assert hook._tap_entry(None, 10, raw.ptr, None) == raw.ptr
    raw.close()


def test_entry_keeps_no_event_alive(monkeypatch):
    hook = _bare_hook()
    monkeypatch.setattr(hook, "_tap_callback", lambda p, t, event, r: event)
    before = _cg_event_proxies()
    for _ in range(200):
        raw = _RawEvent()
        hook._tap_entry(None, 10, raw.ptr, None)
        # Back to the tap's own retain only: the call's proxy is gone.
        assert raw.retains() == 1
        raw.close()
    assert _cg_event_proxies() == before


def test_entry_proxy_keeps_a_deferred_event_alive(monkeypatch):
    hook = _bare_hook()
    kept = []

    def defer(proxy, event_type, event, refcon):
        kept.append(event)
        return None
    monkeypatch.setattr(hook, "_tap_callback", defer)
    raw = _RawEvent()
    assert hook._tap_entry(None, 10, raw.ptr, None) is None
    # The queued proxy holds its own retain, so the event survives the tap
    # releasing its reference after a NULL return.
    assert raw.retains() == 2
    raw.close()
    kept.clear()
