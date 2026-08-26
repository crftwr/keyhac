"""macOS keyboard hook - CGEventTap via PyObjC.

Faithful port of keyhac-mac Keyhac/ExtensionApiLayer/KeyhacCore_Hook.swift:
- session event tap (head insert, default/active tap), mask keyDown|keyUp|flagsChanged
- two private CGEventSources to classify events: "translated" (Keyhac output,
  never re-processed) and "replay" (re-enters the keymap)
- flagsChanged is normalized to down/up by diffing the per-key device flag
- event ordering: real events arriving while injected events are in flight are
  deferred and re-posted once the injected batch drains (0.2 s watchdog)
- passthrough events get their modifier flags rewritten from the tracked
  virtual modifier state
- a periodic timer re-enables the tap if the OS disabled it (timeout)
- mouse output (send_mouse / cursor_pos - new in keyhac2, keyhac-mac had
  none): CGEvent mouse events posted from the same private sources; with
  on_mouse wired, the tap also watches button-down/scroll types for
  one-shot cancellation (WH_MOUSE_LL parity), classified by source like
  keys, never consumed, never deferred

Deviations from the Swift original (deliberate fixes):
- the sanity-check countdown constants were swapped upstream
  (KeyhacCore_Hook.swift:398-403); here the check really runs once per second
- NX_CONTROLMASK is included in the stripped modifier mask (upstream used
  NX_COMMANDMASK twice and never stripped the generic control flag)
- re-posted deferred real events are tagged (kCGEventSourceUserData) and
  counted in flight; fresh real events keep deferring until every re-post has
  returned through the tap.  Upstream clears the deferred list on flush and
  forgets the re-posts, so a real event arriving during their round-trip was
  processed ahead of earlier real input - observed live as a few percent of
  tools/hook_echo.py --stress-ordering rounds, always transposing the flush
  window's last keystrokes.
"""

import ctypes
import time
from typing import Callable, Sequence

import Quartz

from keyhac.platform.base import InputHook, KeyEvent
from keyhac.core.const import (
    MODKEY_ALT, MODKEY_ALT_L, MODKEY_ALT_R,
    MODKEY_CMD, MODKEY_CMD_L, MODKEY_CMD_R,
    MODKEY_CTRL, MODKEY_CTRL_L, MODKEY_CTRL_R,
    MODKEY_SHIFT, MODKEY_SHIFT_L, MODKEY_SHIFT_R,
)
from keyhac.core import log

logger = log.getLogger("MacHook")

# IOKit/IOLLEvent.h device-dependent modifier flags
NX_DEVICELCTLKEYMASK = 0x00000001
NX_DEVICELSHIFTKEYMASK = 0x00000002
NX_DEVICERSHIFTKEYMASK = 0x00000004
NX_DEVICELCMDKEYMASK = 0x00000008
NX_DEVICERCMDKEYMASK = 0x00000010
NX_DEVICELALTKEYMASK = 0x00000020
NX_DEVICERALTKEYMASK = 0x00000040
NX_DEVICERCTLKEYMASK = 0x00002000
# device-independent flags
NX_ALPHASHIFTMASK = 0x00010000
NX_SHIFTMASK = 0x00020000
NX_CONTROLMASK = 0x00040000
NX_ALTERNATEMASK = 0x00080000
NX_COMMANDMASK = 0x00100000
NX_SECONDARYFNMASK = 0x00800000

# All modifier flags Keyhac owns on passthrough events
MODIFIER_FLAGS_MASK = (
    NX_DEVICELCTLKEYMASK | NX_DEVICERCTLKEYMASK | NX_CONTROLMASK
    | NX_DEVICELSHIFTKEYMASK | NX_DEVICERSHIFTKEYMASK | NX_SHIFTMASK
    | NX_DEVICELALTKEYMASK | NX_DEVICERALTKEYMASK | NX_ALTERNATEMASK
    | NX_DEVICELCMDKEYMASK | NX_DEVICERCMDKEYMASK | NX_COMMANDMASK
    | NX_SECONDARYFNMASK
)

VK_CAPITAL = 0x39

# kCGEventSourceUserData value marking a deferred real event we re-posted.
# Hardware events carry 0; a foreign synthetic event colliding with this
# exact value would merely skip one deferral, so no stronger scheme is needed.
REPOST_TAG = 0x4B484143  # "KHAC"

# modifier key code -> its device flag
_KEYCODE_FLAGS = {
    0x3B: NX_DEVICELCTLKEYMASK,    # Left Control
    0x3E: NX_DEVICERCTLKEYMASK,    # Right Control
    0x38: NX_DEVICELSHIFTKEYMASK,  # Left Shift
    0x3C: NX_DEVICERSHIFTKEYMASK,  # Right Shift
    0x37: NX_DEVICELCMDKEYMASK,    # Left Command
    0x36: NX_DEVICERCMDKEYMASK,    # Right Command
    0x3A: NX_DEVICELALTKEYMASK,    # Left Option
    0x3D: NX_DEVICERALTKEYMASK,    # Right Option
    0x3F: NX_SECONDARYFNMASK,      # Fn
    0x39: NX_ALPHASHIFTMASK,       # Caps Lock
}


# Mouse event types that cancel a pending one-shot modifier when physical
# (button downs + wheel; plain movement deliberately does not — keyhac-win
# behavior — and tapping kCGEventMouseMoved would put Python in the path of
# every pointer movement).
_MOUSE_CANCEL_TYPES = frozenset({
    Quartz.kCGEventLeftMouseDown,
    Quartz.kCGEventRightMouseDown,
    Quartz.kCGEventOtherMouseDown,
    Quartz.kCGEventScrollWheel,
})

# send_mouse button vocabulary -> (event type, CGMouseButton)
_MOUSE_BUTTON_EVENTS = {
    ("left", True): (Quartz.kCGEventLeftMouseDown, Quartz.kCGMouseButtonLeft),
    ("left", False): (Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft),
    ("right", True): (Quartz.kCGEventRightMouseDown, Quartz.kCGMouseButtonRight),
    ("right", False): (Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
    ("middle", True): (Quartz.kCGEventOtherMouseDown, Quartz.kCGMouseButtonCenter),
    ("middle", False): (Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
}

# Injected motion while a button is held must be that button's *dragged*
# event type - a plain mouse-moved with a button down is ignored by most
# apps' drag tracking.
_MOUSE_DRAG_EVENTS = (
    (Quartz.kCGMouseButtonLeft, Quartz.kCGEventLeftMouseDragged),
    (Quartz.kCGMouseButtonRight, Quartz.kCGEventRightMouseDragged),
    (Quartz.kCGMouseButtonCenter, Quartz.kCGEventOtherMouseDragged),
)


def _virtual_modifier_to_event_flags(src: int) -> int:
    dst = src
    if src & (NX_DEVICELCTLKEYMASK | NX_DEVICERCTLKEYMASK):
        dst |= NX_CONTROLMASK
    if src & (NX_DEVICELSHIFTKEYMASK | NX_DEVICERSHIFTKEYMASK):
        dst |= NX_SHIFTMASK
    if src & (NX_DEVICELALTKEYMASK | NX_DEVICERALTKEYMASK):
        dst |= NX_ALTERNATEMASK
    if src & (NX_DEVICELCMDKEYMASK | NX_DEVICERCMDKEYMASK):
        dst |= NX_COMMANDMASK
    if src & NX_SECONDARYFNMASK:
        dst |= NX_SECONDARYFNMASK
    return dst


class MacInputHook(InputHook):

    TIMER_INTERVAL = 0.0333
    SANITY_CHECK_INTERVAL = 1.0
    FLUSH_REAL_KEY_EVENTS_TIMEOUT = 0.2

    # One wheel "notch" (the keyhac config unit, from keyhac-win where a
    # notch is WHEEL_DELTA) injected as CG scroll lines. 3 lines matches the
    # Windows default lines-per-notch, so a migrated config scrolls about
    # the same distance on both OSes.
    LINES_PER_NOTCH = 3

    # Successive injected downs of one button within this window escalate
    # kCGMouseEventClickState (1, 2, 3...), which is how macOS apps detect
    # double-clicks on synthetic input - the OS click timer only serves
    # hardware events. Fixed value rather than NSEvent.doubleClickInterval
    # to keep AppKit out of the hook; any intervening move resets the run,
    # approximating the OS movement-slop rule.
    DOUBLE_CLICK_INTERVAL = 0.5

    def __init__(self):
        self._on_key: Callable[[KeyEvent], bool] | None = None
        self._on_restored: Callable[[], None] | None = None
        self._on_mouse: Callable[[], None] | None = None

        # Mouse output state: buttons we injected down (motion between a
        # down and its up must be posted as dragged events), and the
        # (button, time, count) of the last injected down for click-state
        # escalation.
        self._mouse_buttons_down: set = set()
        self._last_click: tuple[str, float, int] | None = None

        self._event_tap = None
        self._run_loop_source = None
        self._timer = None
        self._source_translated = None
        self._source_replay = None
        self._source_translated_id = None
        self._source_replay_id = None

        # Event order handling
        self._num_pending_virtual = 0
        self._num_pending_reposts = 0
        self._deferred_real_events = []
        self._flush_countdown = 0.0
        self._sanity_countdown = MacInputHook.SANITY_CHECK_INTERVAL

        # Virtual modifier state (device flag bits) for passthrough rewriting
        self._virtual_modifier = 0

    # ------------------------------------------------------------------

    def install(self, on_key, on_restored, on_mouse=None) -> None:

        if self._event_tap is not None:
            logger.warning("Keyboard hook is already installed.")
            return

        self._on_key = on_key
        self._on_restored = on_restored
        self._on_mouse = on_mouse

        event_mask = (
            (1 << Quartz.kCGEventKeyDown)
            | (1 << Quartz.kCGEventKeyUp)
            | (1 << Quartz.kCGEventFlagsChanged)
        )
        if on_mouse is not None:
            # One-shot cancellation (keyhac-win parity; keyhac-mac never had
            # it): tap the cancel types too. Motion is deliberately not
            # tapped, so mouse events never join the key deferral queue -
            # a deferred click would be re-posted after moves that followed
            # it (see the mouse branch in _tap_callback).
            for cancel_type in _MOUSE_CANCEL_TYPES:
                event_mask |= 1 << cancel_type

        self._event_tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            event_mask,
            self._tap_callback,
            None,
        )
        if self._event_tap is None:
            raise RuntimeError(
                "Failed to create the event tap. "
                "Check Accessibility permission (System Settings > Privacy & Security > Accessibility).")

        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._event_tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), self._run_loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._event_tap, True)

        self._source_translated = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        self._source_replay = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        self._source_translated_id = Quartz.CGEventSourceGetSourceStateID(self._source_translated)
        self._source_replay_id = Quartz.CGEventSourceGetSourceStateID(self._source_replay)

        self._num_pending_virtual = 0
        self._num_pending_reposts = 0
        self._deferred_real_events = []

        self._timer = Quartz.CFRunLoopTimerCreate(
            None,
            Quartz.CFAbsoluteTimeGetCurrent() + MacInputHook.TIMER_INTERVAL,
            MacInputHook.TIMER_INTERVAL,
            0, 0,
            self._on_timer,
            None,
        )
        Quartz.CFRunLoopAddTimer(
            Quartz.CFRunLoopGetCurrent(), self._timer, Quartz.kCFRunLoopCommonModes)

        logger.info("Keyboard hook installed.")

    def uninstall(self) -> None:
        if self._event_tap is None:
            return
        if self._timer is not None:
            Quartz.CFRunLoopTimerInvalidate(self._timer)
            self._timer = None
        Quartz.CGEventTapEnable(self._event_tap, False)
        if self._run_loop_source is not None:
            Quartz.CFRunLoopRemoveSource(
                Quartz.CFRunLoopGetCurrent(), self._run_loop_source, Quartz.kCFRunLoopCommonModes)
            self._run_loop_source = None
        self._event_tap = None
        self._source_translated = None
        self._source_replay = None
        self._num_pending_virtual = 0
        self._num_pending_reposts = 0
        self._deferred_real_events = []
        self._mouse_buttons_down = set()
        self._last_click = None
        logger.info("Keyboard hook uninstalled.")

    @property
    def installed(self) -> bool:
        return self._event_tap is not None

    # ------------------------------------------------------------------

    def _tap_callback(self, proxy, event_type, event, refcon):

        # The OS disables a stalled tap; recover immediately.
        if event_type in (Quartz.kCGEventTapDisabledByTimeout,
                          Quartz.kCGEventTapDisabledByUserInput):
            self._restore_hook()
            return event

        source_id = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceStateID)
        if source_id == self._source_translated_id:
            kind = "translated"
        elif source_id == self._source_replay_id:
            kind = "replay"
        else:
            kind = "real"

        # Mouse events (tapped only when on_mouse is wired): observation
        # only, mirroring the Windows WH_MOUSE_LL rule - physical button
        # downs and wheel turns cancel a pending one-shot; our own injected
        # mouse output does not. Never consumed and never deferred: the
        # deferral queue orders *keyboard* events, and motion is not tapped,
        # so a deferred click would be re-posted after moves that followed
        # it. (Injected mouse events are not counted in flight either -
        # see send_mouse.)
        if event_type in _MOUSE_CANCEL_TYPES:
            if kind == "real" and self._on_mouse is not None:
                try:
                    self._on_mouse(
                        "wheel" if event_type == Quartz.kCGEventScrollWheel
                        else "button")
                except Exception:
                    logger.error("Mouse handler raised; event passed through.")
            return event

        key_code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)

        # Event order handling: postpone real events while injected events or
        # re-posted deferred reals are in flight (or earlier real events are
        # already postponed).  A returning re-post is recognized by its tag
        # and passes - it is earlier input coming back in order.
        is_repost = False
        if kind == "real":
            is_repost = (Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGEventSourceUserData) == REPOST_TAG)
            if is_repost:
                self._num_pending_reposts = max(self._num_pending_reposts - 1, 0)
            elif (self._num_pending_virtual > 0 or self._num_pending_reposts > 0
                    or self._deferred_real_events):
                self._deferred_real_events.append(event)
                return None

        while True:  # single-pass block, breakable like Swift's labeled do
            # Determine direction
            if event_type == Quartz.kCGEventKeyDown:
                down = True
            elif event_type == Quartz.kCGEventKeyUp:
                down = False
            elif event_type == Quartz.kCGEventFlagsChanged:
                # CapsLock is special (toggle) - skip processing
                if key_code == VK_CAPITAL:
                    break
                changed_flags = _KEYCODE_FLAGS.get(key_code, 0)
                if changed_flags == 0:
                    # A flagsChanged for a keycode outside the modifier map
                    # changes no flag this engine tracks, so passing it
                    # through untouched is the whole handling. macOS does
                    # emit these - the screenshot UI posts one with vk=0
                    # (issue #30) - and warning about them was only noise.
                    break
                down = bool(Quartz.CGEventGetFlags(event) & changed_flags)
            else:
                logger.warning(f"Unexpected event type - {event_type}")
                break

            if kind in ("real", "replay") and self._on_key is not None:
                try:
                    consumed = self._on_key(KeyEvent(int(key_code), down, kind))
                except Exception:
                    logger.error("Key handler raised; passing event through.")
                    consumed = False
                if consumed:
                    # Dispose the event - it was handled by the keymap
                    Quartz.CGEventSetType(event, Quartz.kCGEventNull)
                    break

            # Passthrough handling
            if event_type in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                # Overwrite modifier flags from the tracked virtual state
                flags = Quartz.CGEventGetFlags(event)
                flags &= ~MODIFIER_FLAGS_MASK
                flags |= _virtual_modifier_to_event_flags(self._virtual_modifier)
                Quartz.CGEventSetFlags(event, flags)
            elif event_type == Quartz.kCGEventFlagsChanged:
                # Track real modifier status changes
                if down:
                    self._virtual_modifier |= _KEYCODE_FLAGS.get(key_code, 0)
                else:
                    self._virtual_modifier &= ~_KEYCODE_FLAGS.get(key_code, 0)
            break

        # Event order handling: process postponed real events once all
        # in-flight events (injected and re-posted) have come back.
        if kind in ("translated", "replay"):
            self._num_pending_virtual = max(self._num_pending_virtual - 1, 0)
        if ((kind in ("translated", "replay") or is_repost)
                and self._num_pending_virtual == 0
                and self._num_pending_reposts == 0):
            self._flush_real_key_events()

        return event

    # ------------------------------------------------------------------

    def send(self, events: Sequence[tuple[int, bool]], replay: bool = False) -> None:
        source = self._source_replay if replay else self._source_translated
        if source is None:
            logger.error("Cannot send key events - hook is not installed.")
            return
        for vk, down in events:
            event = Quartz.CGEventCreateKeyboardEvent(source, vk, down)
            self._num_pending_virtual += 1
            self._flush_countdown = MacInputHook.FLUSH_REAL_KEY_EVENTS_TIMEOUT
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def send_text(self, s: str) -> None:
        """Type a literal string via CGEventKeyboardSetUnicodeString
        (chunked - the API accepts ~20 UTF-16 units per event)."""
        if self._source_translated is None:
            logger.error("Cannot send text - hook is not installed.")
            return
        for i in range(0, len(s), 20):
            chunk = s[i:i + 20]
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(self._source_translated, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
                self._num_pending_virtual += 1
                self._flush_countdown = MacInputHook.FLUSH_REAL_KEY_EVENTS_TIMEOUT
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    # ------------------------------------------------------------------

    def char_for_key(self, vk: int, mod: int = 0) -> str | None:
        """The character `vk` produces on the active layout (InputHook API).

        A CGEvent carrying the key code and the glyph-changing modifiers is
        handed to AppKit, whose `NSEvent.characters` is the same translation
        the OS performs for a real keystroke - so the answer follows whatever
        layout is selected, with no layout table of our own to go stale.
        Cheaper and far shorter than the Carbon route (`UCKeyTranslate` plus
        `TISCopyCurrentKeyboardLayoutInputSource`), and verified to honour
        the modifier flags.

        lazydocs: ignore
        """
        if mod & (MODKEY_CTRL | MODKEY_CTRL_L | MODKEY_CTRL_R
                  | MODKEY_CMD | MODKEY_CMD_L | MODKEY_CMD_R):
            return None
        flags = 0
        if mod & (MODKEY_SHIFT | MODKEY_SHIFT_L | MODKEY_SHIFT_R):
            flags |= Quartz.kCGEventFlagMaskShift
        if mod & (MODKEY_ALT | MODKEY_ALT_L | MODKEY_ALT_R):
            flags |= Quartz.kCGEventFlagMaskAlternate
        try:
            from AppKit import NSEvent
            event = Quartz.CGEventCreateKeyboardEvent(None, vk, True)
            if event is None:
                return None
            if flags:
                Quartz.CGEventSetFlags(event, flags)
            text = NSEvent.eventWithCGEvent_(event).characters()
        except Exception:
            return None
        if not text or len(text) != 1 or not text.isprintable():
            return None
        return str(text)

    def cursor_pos(self) -> tuple[int, int]:
        """Cursor position in CG global coordinates (top-left of the main
        display, y down) - the same space CGEventPost positions mouse events
        in and screen_frames() reports, so it is already the portable
        top-left contract."""
        point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return (int(round(point.x)), int(round(point.y)))

    def send_mouse(self, events, replay: bool = False) -> None:
        """Inject mouse events (see InputHook.send_mouse for the item
        vocabulary). CG mouse events are inherently absolute, so relative
        moves accumulate onto the real cursor position - the same
        relative-as-absolute scheme the Windows side uses against pointer
        acceleration, only here it falls out of the API.

        Unlike send(), nothing is counted in flight: motion and button-up
        types are not in the tap mask (they could never be counted back
        down), and posts from this thread enter the HID stream in post
        order anyway - the ordering assumption send() already relies on
        within a batch - so the key-deferral machinery is not needed for
        the mouse channel."""
        source = self._source_replay if replay else self._source_translated
        if source is None:
            logger.error("Cannot send mouse events - hook is not installed.")
            return
        pos = None  # running cursor position across the batch
        for event in events:
            kind = event[0]
            if kind == "move":
                if pos is None:
                    pos = self.cursor_pos()
                dx, dy = int(event[1]), int(event[2])
                pos = (pos[0] + dx, pos[1] + dy)
                event_type, button = self._motion_event_type()
                cg = Quartz.CGEventCreateMouseEvent(source, event_type, pos, button)
                # Hardware motion carries per-event deltas; some apps (games,
                # pointer-lock) read those instead of the position.
                Quartz.CGEventSetIntegerValueField(cg, Quartz.kCGMouseEventDeltaX, dx)
                Quartz.CGEventSetIntegerValueField(cg, Quartz.kCGMouseEventDeltaY, dy)
                self._last_click = None  # movement breaks a multi-click run
            elif kind in ("wheel", "hwheel"):
                lines = float(event[1]) * MacInputHook.LINES_PER_NOTCH
                # CG sign conventions: wheel1 positive scrolls up (away from
                # the user) - matching the portable contract - and wheel2
                # positive scrolls *left*, so the positive-right contract
                # negates it.
                v = lines if kind == "wheel" else 0.0
                h = -lines if kind == "hwheel" else 0.0
                cg = Quartz.CGEventCreateScrollWheelEvent(
                    source, Quartz.kCGScrollEventUnitLine, 2, int(v), int(h))
                # The integer line counts truncate; the fixed-point fields
                # carry the exact value for smooth-scrolling consumers (and
                # make fractional notches mean something).
                Quartz.CGEventSetDoubleValueField(
                    cg, Quartz.kCGScrollWheelEventFixedPtDeltaAxis1, v)
                Quartz.CGEventSetDoubleValueField(
                    cg, Quartz.kCGScrollWheelEventFixedPtDeltaAxis2, h)
            else:
                try:
                    event_type, button = _MOUSE_BUTTON_EVENTS[(kind, bool(event[1]))]
                except KeyError:
                    raise ValueError(f"Unknown mouse event: {event!r}") from None
                down = bool(event[1])
                if pos is None:
                    pos = self.cursor_pos()
                cg = Quartz.CGEventCreateMouseEvent(source, event_type, pos, button)
                Quartz.CGEventSetIntegerValueField(
                    cg, Quartz.kCGMouseEventClickState, self._click_state(kind, down))
                if down:
                    self._mouse_buttons_down.add(button)
                else:
                    self._mouse_buttons_down.discard(button)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)

    def _motion_event_type(self):
        """(event type, button) for injected motion: the dragged type of a
        held button - ours, or a physically held one via the combined
        session button state - else plain mouse-moved."""
        for button, drag_type in _MOUSE_DRAG_EVENTS:
            if button in self._mouse_buttons_down or Quartz.CGEventSourceButtonState(
                    Quartz.kCGEventSourceStateCombinedSessionState, button):
                return drag_type, button
        return Quartz.kCGEventMouseMoved, Quartz.kCGMouseButtonLeft

    def _click_state(self, name: str, down: bool) -> int:
        """kCGMouseEventClickState for an injected button event: downs of the
        same button within DOUBLE_CLICK_INTERVAL escalate 1 -> 2 -> 3...; the
        matching up mirrors its down's count (an up whose down we never sent
        counts 1)."""
        if down:
            now = time.monotonic()
            last = self._last_click
            if (last is not None and last[0] == name
                    and now - last[1] <= MacInputHook.DOUBLE_CLICK_INTERVAL):
                count = last[2] + 1
            else:
                count = 1
            self._last_click = (name, now, count)
            return count
        last = self._last_click
        return last[2] if last is not None and last[0] == name else 1

    # ------------------------------------------------------------------

    def _on_timer(self, timer, info=None):
        # Sanity check: once per second, re-enable the tap if the OS
        # disabled it. (The Swift original swapped these constants and
        # effectively checked every tick - fixed here.)
        self._sanity_countdown -= MacInputHook.TIMER_INTERVAL
        if self._sanity_countdown <= 0.0:
            self._sanity_countdown = MacInputHook.SANITY_CHECK_INTERVAL
            if self._event_tap is not None and not Quartz.CGEventTapIsEnabled(self._event_tap):
                self._restore_hook()

        # Watchdog for deferred real events
        if self._flush_countdown > 0.0:
            self._flush_countdown -= MacInputHook.TIMER_INTERVAL
            if self._flush_countdown <= 0.0:
                self._flush_real_key_events()

    def _restore_hook(self):
        self._num_pending_virtual = 0
        self._num_pending_reposts = 0
        self._deferred_real_events = []
        if self._event_tap is not None:
            Quartz.CGEventTapEnable(self._event_tap, True)
        if self._on_restored is not None:
            self._on_restored()

    def _flush_real_key_events(self):
        """Re-post the deferred real events.  Called when everything in flight
        has drained, or by the watchdog when something never returned (which
        is also why the counters are cleared unconditionally here)."""
        self._num_pending_virtual = 0
        self._num_pending_reposts = 0
        if self._deferred_real_events:
            deferred = self._deferred_real_events
            self._deferred_real_events = []
            for event in deferred:
                Quartz.CGEventSetIntegerValueField(
                    event, Quartz.kCGEventSourceUserData, REPOST_TAG)
                self._num_pending_reposts += 1
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            # The watchdog now also guards the re-posts themselves.
            self._flush_countdown = MacInputHook.FLUSH_REAL_KEY_EVENTS_TIMEOUT

    # ------------------------------------------------------------------

    def keyboard_layout(self) -> str:
        """Physical keyboard layout via Carbon KBGetLayoutType (four-char
        codes 'ANSI' / 'JIS ' / 'ISO '; verified against the framework binary
        since the constants were removed from modern SDK headers)."""
        try:
            carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            carbon.LMGetKbdType.restype = ctypes.c_uint8
            carbon.KBGetLayoutType.restype = ctypes.c_uint32
            carbon.KBGetLayoutType.argtypes = [ctypes.c_int16]
            layout_type = carbon.KBGetLayoutType(carbon.LMGetKbdType())
        except Exception:
            logger.error("Failed to detect keyboard layout; assuming ANSI.")
            return "ansi"

        if layout_type == int.from_bytes(b"ANSI", "big"):
            return "ansi"
        if layout_type == int.from_bytes(b"JIS ", "big"):
            return "jis"
        if layout_type == int.from_bytes(b"ISO ", "big"):
            logger.error("Unsupported keyboard layout: iso")
            return "iso"
        logger.error(f"Unknown keyboard layout type: {layout_type:#x}; assuming ANSI.")
        return "ansi"
