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
from typing import Callable, Sequence

import Quartz

from keyhac.platform.base import InputHook, KeyEvent
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

    def __init__(self):
        self._on_key: Callable[[KeyEvent], bool] | None = None
        self._on_restored: Callable[[], None] | None = None

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

    def install(self, on_key, on_restored) -> None:

        if self._event_tap is not None:
            logger.warning("Keyboard hook is already installed.")
            return

        self._on_key = on_key
        self._on_restored = on_restored

        event_mask = (
            (1 << Quartz.kCGEventKeyDown)
            | (1 << Quartz.kCGEventKeyUp)
            | (1 << Quartz.kCGEventFlagsChanged)
        )

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

        key_code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)

        source_id = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceStateID)
        if source_id == self._source_translated_id:
            kind = "translated"
        elif source_id == self._source_replay_id:
            kind = "replay"
        else:
            kind = "real"

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
                    logger.warning(f"Flag changed for unknown reason - vk={key_code}")
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
