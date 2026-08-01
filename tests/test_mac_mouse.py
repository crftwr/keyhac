"""macOS mouse injection (MacInputHook.send_mouse / cursor_pos).

The translation layer - item vocabulary to CGEvent construction - is unit
tested against faked Quartz calls, like test_mac_hook.py. The live section
briefly moves the real cursor (and restores it); it skips rather than fails
when the environment refuses event posting (no Accessibility grant, e.g. a
sandboxed shell). Wheel *direction* and app-visible click/drag semantics
need an interactive pass - see doc/05-features.md.
"""

import sys
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

TRANSLATED_ID = 1001
REPLAY_ID = 1002


class FakeMouseEvent:
    def __init__(self, kind, source, event_type, pos=None, button=None,
                 wheel=None):
        self.kind = kind                  # "mouse" | "wheel"
        self.source_id = source.state_id
        self.event_type = event_type
        self.pos = pos
        self.button = button
        self.wheel = wheel                # (int v, int h) as constructed
        self.fields = {}                  # int/double fields set afterwards


class MouseHarness:
    """MacInputHook with the Quartz mouse calls faked; drives send_mouse."""

    def __init__(self, monkeypatch, cursor=(100, 200)):
        import Quartz
        from keyhac.platform.mac import hook as hook_module

        self.Q = Quartz
        self.posted = []
        self.buttons_physical = set()     # CGMouseButton values "held"
        self.now = 1000.0                 # fake monotonic clock

        def create_mouse(source, event_type, pos, button):
            return FakeMouseEvent("mouse", source, event_type,
                                  pos=tuple(pos), button=button)

        def create_wheel(source, unit, count, v, h):
            assert unit == Quartz.kCGScrollEventUnitLine
            assert count == 2
            return FakeMouseEvent("wheel", source, Quartz.kCGEventScrollWheel,
                                  wheel=(v, h))

        def set_int(event, field, value):
            event.fields[field] = value

        def set_double(event, field, value):
            event.fields[field] = value

        monkeypatch.setattr(Quartz, "CGEventCreateMouseEvent", create_mouse)
        monkeypatch.setattr(Quartz, "CGEventCreateScrollWheelEvent", create_wheel)
        monkeypatch.setattr(Quartz, "CGEventSetIntegerValueField", set_int)
        monkeypatch.setattr(Quartz, "CGEventSetDoubleValueField", set_double)
        monkeypatch.setattr(Quartz, "CGEventPost",
                            lambda tap, e: self.posted.append(e))
        monkeypatch.setattr(
            Quartz, "CGEventSourceButtonState",
            lambda state, button: button in self.buttons_physical)
        monkeypatch.setattr(hook_module.time, "monotonic", lambda: self.now)

        self.hook = hook_module.MacInputHook()
        self.hook._source_translated = SimpleNamespace(state_id=TRANSLATED_ID)
        self.hook._source_replay = SimpleNamespace(state_id=REPLAY_ID)
        monkeypatch.setattr(self.hook, "cursor_pos", lambda: cursor)


@pytest.fixture
def m(monkeypatch):
    return MouseHarness(monkeypatch)


# -- buttons ---------------------------------------------------------------

def test_button_events_land_at_the_cursor(m):
    m.hook.send_mouse([("left", True), ("left", False)])
    down, up = m.posted
    assert (down.event_type, down.button) == (
        m.Q.kCGEventLeftMouseDown, m.Q.kCGMouseButtonLeft)
    assert (up.event_type, up.button) == (
        m.Q.kCGEventLeftMouseUp, m.Q.kCGMouseButtonLeft)
    assert down.pos == up.pos == (100, 200)
    assert down.source_id == TRANSLATED_ID


def test_right_and_middle_buttons_map(m):
    m.hook.send_mouse([("right", True), ("middle", True)])
    right, middle = m.posted
    assert (right.event_type, right.button) == (
        m.Q.kCGEventRightMouseDown, m.Q.kCGMouseButtonRight)
    assert (middle.event_type, middle.button) == (
        m.Q.kCGEventOtherMouseDown, m.Q.kCGMouseButtonCenter)


def test_click_state_escalates_within_the_double_click_window(m):
    m.hook.send_mouse([("left", True), ("left", False)])
    m.now += 0.2
    m.hook.send_mouse([("left", True), ("left", False)])
    states = [e.fields[m.Q.kCGMouseEventClickState] for e in m.posted]
    assert states == [1, 1, 2, 2]        # second click reads as a double


def test_click_state_resets_after_the_window_or_a_move(m):
    m.hook.send_mouse([("left", True), ("left", False)])
    m.now += 1.0                          # window expired
    m.hook.send_mouse([("left", True), ("left", False)])
    m.now += 0.2
    m.hook.send_mouse([("move", 5, 0)])   # movement breaks the run
    m.hook.send_mouse([("left", True)])
    states = [e.fields[m.Q.kCGMouseEventClickState]
              for e in m.posted if e.kind == "mouse" and e.button is not None
              and e.event_type != m.Q.kCGEventMouseMoved]
    assert states == [1, 1, 1, 1, 1]


def test_click_state_tracks_per_button(m):
    m.hook.send_mouse([("left", True), ("left", False),
                       ("right", True), ("right", False)])
    states = [e.fields[m.Q.kCGMouseEventClickState] for e in m.posted]
    assert states == [1, 1, 1, 1]        # right is a fresh run, not left's 2nd


# -- movement ---------------------------------------------------------------

def test_moves_accumulate_relative_onto_the_cursor(m):
    m.hook.send_mouse([("move", 10, 5), ("move", -3, 2)])
    first, second = m.posted
    assert first.event_type == m.Q.kCGEventMouseMoved
    assert first.pos == (110, 205)
    assert second.pos == (107, 207)
    assert first.fields[m.Q.kCGMouseEventDeltaX] == 10
    assert first.fields[m.Q.kCGMouseEventDeltaY] == 5
    assert second.fields[m.Q.kCGMouseEventDeltaX] == -3


def test_move_between_our_down_and_up_is_a_drag(m):
    m.hook.send_mouse([("left", True), ("move", 10, 0), ("left", False),
                       ("move", 10, 0)])
    _down, drag, _up, move = m.posted
    assert (drag.event_type, drag.button) == (
        m.Q.kCGEventLeftMouseDragged, m.Q.kCGMouseButtonLeft)
    assert move.event_type == m.Q.kCGEventMouseMoved  # released again


def test_move_while_a_physical_button_is_held_is_a_drag(m):
    m.buttons_physical.add(m.Q.kCGMouseButtonRight)
    m.hook.send_mouse([("move", 1, 1)])
    assert (m.posted[0].event_type, m.posted[0].button) == (
        m.Q.kCGEventRightMouseDragged, m.Q.kCGMouseButtonRight)


def test_buttons_after_a_move_land_at_the_moved_position(m):
    m.hook.send_mouse([("move", 50, 0), ("left", True), ("left", False)])
    assert [e.pos for e in m.posted] == [(150, 200)] * 3


# -- wheels ------------------------------------------------------------------

def test_wheel_scrolls_lines_per_notch_up_positive(m):
    m.hook.send_mouse([("wheel", 2)])
    (event,) = m.posted
    assert event.wheel == (6, 0)          # 2 notches * 3 lines, away = up = +
    assert event.fields[m.Q.kCGScrollWheelEventFixedPtDeltaAxis1] == 6.0
    assert event.fields[m.Q.kCGScrollWheelEventFixedPtDeltaAxis2] == 0.0


def test_hwheel_positive_right_negates_cg_left_positive(m):
    m.hook.send_mouse([("hwheel", 1)])
    (event,) = m.posted
    assert event.wheel == (0, -3)
    assert event.fields[m.Q.kCGScrollWheelEventFixedPtDeltaAxis2] == -3.0


def test_fractional_notches_survive_in_the_fixed_point_fields(m):
    m.hook.send_mouse([("wheel", 0.5)])
    (event,) = m.posted
    assert event.wheel == (1, 0)          # int construction truncates
    assert event.fields[m.Q.kCGScrollWheelEventFixedPtDeltaAxis1] == 1.5


# -- batch semantics ---------------------------------------------------------

def test_replay_events_carry_the_replay_source(m):
    m.hook.send_mouse([("left", True)], replay=True)
    assert m.posted[0].source_id == REPLAY_ID


def test_unknown_item_raises(m):
    with pytest.raises(ValueError):
        m.hook.send_mouse([("side", True)])


def test_uninstalled_hook_logs_and_posts_nothing(m):
    m.hook._source_translated = None
    m.hook.send_mouse([("left", True)])   # must not raise
    assert m.posted == []


def test_mouse_batch_does_not_touch_the_key_flight_ledger(m):
    m.hook.send_mouse([("move", 1, 1), ("left", True), ("left", False),
                       ("wheel", 1)])
    assert m.hook._num_pending_virtual == 0
    assert m.hook._flush_countdown == 0.0


# -- live (skips without Accessibility) --------------------------------------

class TestLive:
    """Posts real events: the cursor genuinely jumps 30 px and is restored."""

    @pytest.fixture
    def live_hook(self):
        from keyhac.platform.mac.hook import MacInputHook
        hook = MacInputHook()
        try:
            hook.install(lambda event: False, lambda: None)
        except RuntimeError:
            pytest.skip("no Accessibility permission in this environment")
        yield hook
        hook.uninstall()

    def test_relative_move_is_exact_and_cursor_pos_agrees(self, live_hook):
        import Quartz
        origin = live_hook.cursor_pos()
        try:
            live_hook.send_mouse([("move", 30, 0)])
            deadline = time.monotonic() + 2.0
            moved = origin
            while moved == origin and time.monotonic() < deadline:
                time.sleep(0.02)
                moved = live_hook.cursor_pos()
            # Relative-as-absolute: exactly 30 px, pointer acceleration
            # cannot distort the distance.
            assert moved == (origin[0] + 30, origin[1])
        finally:
            Quartz.CGWarpMouseCursorPosition(origin)
