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
