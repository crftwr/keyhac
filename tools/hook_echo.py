"""Hook echo tool - the M0 spike, kept for platform bring-up.

Installs the keyboard hook and prints every event with its consume decision
(always pass-through), plus the current focus on each key-down.  No keymap,
no config - this isolates the platform layer.

Run:  python tools/hook_echo.py
Quit: Ctrl+C

This must be the FIRST thing run in any new platform bring-up (especially
the first Windows session - the win backend has not been executed yet).

--stress-ordering [ROUNDS] additionally verifies the InputHook.send ordering
contract (platform/base.py) by deterministically manufacturing the
injected-vs-physical race: each round send()s a replay-tagged batch through
the hook while a background thread posts "pseudo-real" events from a foreign
source - a third private CGEventSource on macOS, SendInput without Keyhac's
dwExtraInfo signature on Windows - which the hook must classify as real input
and keep behind the whole batch (deferral machinery on macOS, input-queue
order on Windows).  A round passes if every batch event is processed before
every pseudo-real one, nothing is lost, and relative order within each group
is intact.  All stress events use F13-F18 and are consumed inside the hook
before they can reach applications - avoid pressing those keys while it runs.
The state-machine counterpart of this check is tests/test_mac_hook.py.
"""

import argparse
import ctypes
import sys
import threading
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from keyhac.core import log
from keyhac.core.vk import init_key_names
from keyhac.platform.base import KeyEvent

log.set_debug(True)
logger = log.getLogger("Echo")


class OrderingStress:
    """One round = send a virtual batch + concurrently post pseudo-real events,
    then check that the batch was processed first, completely, and in order."""

    NUM_VIRTUAL = 20   # 10 down/up pairs, replay-tagged (consumed in on_key)
    ROUND_TIMEOUT = 2.0
    # Quiescence barrier: a round starts only after this much stress-key
    # silence.  The OS occasionally delivers the last posted event a few
    # hundred ms late; without the barrier such a straggler crosses into the
    # next round, is (correctly) deferred behind that round's batch, and the
    # round oracle would misread faithful ordering as a violation.
    QUIET_BEFORE_ROUND = 0.5

    def __init__(self, hook, loop, platform_name, rounds):
        self.hook = hook
        self.loop = loop
        self.rounds = rounds
        self.round = 0
        self.passed = 0
        self.active = False
        self.seq = []          # ("virtual" | "real", vk, down) in processed order
        self.deadline = 0.0
        self._last_stress = 0.0

        # macOS posts the batch chunked so arrivals genuinely interleave and
        # the deferral machinery has to repair the order.  Windows must post
        # ONE SendInput batch: the OS guarantee is per-call atomicity, so
        # interleaving between separate chunks would be legal queue order and
        # the round would misreport it as a violation.
        self.chunked_send = platform_name == "mac"
        if platform_name == "mac":
            self.virtual_vk = 0x69                                # F13
            self.real_vks = [0x6B, 0x71, 0x6A, 0x40, 0x4F]        # F14..F18
            self._post_real = self._make_post_real_mac()
        else:
            self.virtual_vk = 0x7C                                # VK_F13
            self.real_vks = [0x7D, 0x7E, 0x7F, 0x80, 0x81]        # VK_F14..F18
            self._post_real = self._make_post_real_win()

        # One down/up pair per distinct vk, so every pseudo-real event is
        # uniquely identifiable and any reordering among them is visible.
        self.expected_reals = [(vk, down)
                               for vk in self.real_vks for down in (True, False)]
        self.num_real = len(self.expected_reals)

    @staticmethod
    def _make_post_real_mac():
        import Quartz
        # A third private source: its state id matches neither of the hook's,
        # so the hook classifies these events as physical ("real") input.
        # The symbols are bound eagerly: PyObjC lazy-module resolution is not
        # thread-safe, and the poster thread racing the main thread's own
        # first resolution dies with a KeyError in objc._lazyimport.
        create = Quartz.CGEventCreateKeyboardEvent
        post_event = Quartz.CGEventPost
        hid_tap = Quartz.kCGHIDEventTap
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)

        def post(vk, down):
            post_event(hid_tap, create(source, vk, down))
        return post

    @staticmethod
    def _make_post_real_win():
        from keyhac.platform.win import hook as win_hook
        # dwExtraInfo 0 is not one of Keyhac's signatures -> classified "real".

        def post(vk, down):
            inputs = (win_hook.INPUT * 1)()
            flags = 0 if down else win_hook.KEYEVENTF_KEYUP
            scan = win_hook.user32.MapVirtualKeyW(vk, win_hook.MAPVK_VK_TO_VSC)
            inputs[0].type = win_hook.INPUT_KEYBOARD
            inputs[0].union.ki = win_hook.KEYBDINPUT(vk, scan, flags, 0, 0)
            win_hook.user32.SendInput(1, inputs, ctypes.sizeof(win_hook.INPUT))
        return post

    # -- called from the hook callback ---------------------------------

    def on_key(self, event: KeyEvent):
        """Consume decision for stress events, None for everything else.
        Stress vks are consumed even outside a round window so that late
        stragglers from a timed-out round never reach applications."""
        if event.vk == self.virtual_vk and event.kind == "replay":
            self._record("virtual", event)
            return True
        if event.vk in self.real_vks:
            self._record("real", event)
            return True
        return None

    def _record(self, kind, event):
        self._last_stress = time.monotonic()
        if self.active:
            self.seq.append((kind, event.vk, event.down))
        else:
            logger.debug(f"straggler consumed between rounds: "
                         f"vk={event.vk:#x} down={event.down}")

    # -- round driver (runs on the event loop) -------------------------

    def start(self):
        logger.info(
            f"Ordering stress: {self.rounds} rounds, {self.NUM_VIRTUAL} virtual "
            f"(F13) vs {self.num_real} pseudo-real (F14-F18) events per round. "
            f"Do not press F13-F18; other keys echo as usual.")
        self.loop.call_later(self.QUIET_BEFORE_ROUND, self._begin_round)

    def _begin_round(self):
        quiet = time.monotonic() - self._last_stress
        if quiet < self.QUIET_BEFORE_ROUND:
            self.loop.call_later(self.QUIET_BEFORE_ROUND - quiet + 0.02,
                                 self._begin_round)
            return
        self.seq = []
        self.active = True
        self.deadline = time.monotonic() + self.ROUND_TIMEOUT
        threading.Thread(target=self._post_reals, daemon=True).start()
        batch = [(self.virtual_vk, i % 2 == 0) for i in range(self.NUM_VIRTUAL)]
        if self.chunked_send:
            # Small chunks with sleeps so the poster thread's events land in
            # the HID system genuinely interleaved with the batch.  This
            # thread is the run loop, so nothing is processed until the whole
            # round has been posted - without the ordering machinery the
            # reals would be handled wherever they arrived, mid-batch.
            for i in range(0, len(batch), 2):
                self.hook.send(batch[i:i + 2], replay=True)
                time.sleep(0.001)
        else:
            self.hook.send(batch, replay=True)
        self.loop.call_later(0.02, self._poll_round)

    def _post_reals(self):
        for vk, down in self.expected_reals:
            self._post_real(vk, down)
            time.sleep(0.001)      # match the pace of the chunked batch

    def _poll_round(self):
        if len(self.seq) >= self.NUM_VIRTUAL + self.num_real:
            self._finish_round(timeout=False)
        elif time.monotonic() > self.deadline:
            self._finish_round(timeout=True)
        else:
            self.loop.call_later(0.02, self._poll_round)

    def _picture(self):
        """V/v = virtual down/up; A-E/a-e = the five real vks, down/up."""
        chars = []
        for kind, vk, down in self.seq:
            if kind == "virtual":
                chars.append("V" if down else "v")
            else:
                letter = "ABCDE"[self.real_vks.index(vk)]
                chars.append(letter if down else letter.lower())
        return "".join(chars)

    def _finish_round(self, timeout):
        self.active = False
        ok, reason = self._evaluate(timeout)
        self.round += 1
        if ok:
            self.passed += 1
            logger.info(f"round {self.round}/{self.rounds}: PASS")
        else:
            logger.error(f"round {self.round}/{self.rounds}: FAIL - {reason}  "
                         f"[{self._picture()}]")
        if self.round >= self.rounds:
            verdict = "PASS" if self.passed == self.rounds else "FAIL"
            logger.info(f"Ordering stress finished: {self.passed}/{self.rounds} "
                        f"rounds passed - {verdict}")
            self.loop.stop()
        else:
            self.loop.call_later(0.05, self._begin_round)

    def _evaluate(self, timeout):
        """The contract: the batch is processed as a contiguous unit (a real
        event that genuinely arrived first may legally precede it), and each
        stream keeps its internal order, with nothing lost."""
        virtuals = [i for i, e in enumerate(self.seq) if e[0] == "virtual"]
        reals = [(vk, down) for kind, vk, down in self.seq if kind == "real"]
        if timeout or len(virtuals) != self.NUM_VIRTUAL or len(reals) != self.num_real:
            return False, (f"lost events ({len(virtuals)}/{self.NUM_VIRTUAL} virtual, "
                           f"{len(reals)}/{self.num_real} real)")
        if max(virtuals) - min(virtuals) != self.NUM_VIRTUAL - 1:
            return False, "a real event was processed inside the virtual batch"
        downs = [self.seq[i][2] for i in virtuals]
        if downs != [i % 2 == 0 for i in range(len(downs))]:
            return False, "virtual events processed out of order"
        if reals != self.expected_reals:
            return False, "real events processed out of order"
        return True, ""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Keyhac2 platform bring-up: echo hook events; "
                    "optionally stress-test injected-vs-real event ordering.")
    parser.add_argument(
        "--stress-ordering", nargs="?", type=int, const=50, default=None,
        metavar="ROUNDS",
        help="race injected batches against pseudo-real events and verify the "
             "InputHook.send ordering contract (default 50 rounds)")
    args = parser.parse_args(argv)

    if sys.platform == "darwin":
        import keyhac.platform.mac as platform_module
        if not platform_module.check_accessibility(prompt=True):
            logger.error("Accessibility permission required; grant and re-run.")
            return 1
        platform_name = "mac"
    elif sys.platform == "win32":
        import keyhac.platform.win as platform_module
        platform_name = "windows"
    else:
        logger.error(f"Unsupported platform: {sys.platform}")
        return 1

    hook, focus_provider, loop = platform_module.create_platform()
    names = init_key_names(platform_name, hook.keyboard_layout())
    logger.info(f"Keyboard layout: {hook.keyboard_layout()}")

    stress = None
    if args.stress_ordering is not None:
        stress = OrderingStress(hook, loop, platform_name, args.stress_ordering)

    def on_key(event: KeyEvent) -> bool:
        if stress is not None:
            decision = stress.on_key(event)
            if decision is not None:
                return decision
        t0 = time.perf_counter()
        name = names.vk_to_str(event.vk)
        focus = focus_provider.get_focus() if event.down else None
        dt = (time.perf_counter() - t0) * 1000
        direction = "D" if event.down else "U"
        line = f"{direction}-{name:12s} vk={event.vk:<4d} kind={event.kind:6s} focus_query={dt:5.2f}ms"
        if focus is not None:
            line += f"  app={focus.app_name!r} title={focus.window_title!r}"
        logger.info(line)
        return False  # never consume

    def on_restored():
        logger.warning("Hook was disabled by the OS and has been restored.")

    hook.install(on_key, on_restored)

    def health_tick():
        hook.check_health()
        loop.call_later(0.1, health_tick)
    loop.call_later(0.1, health_tick)

    if stress is not None:
        loop.call_later(1.0, stress.start)

    import signal
    signal.signal(signal.SIGINT, lambda sig, frame: loop.stop())

    if stress is None:
        logger.info("Echoing key events. Ctrl+C to quit.")
    try:
        loop.run()
    finally:
        hook.uninstall()
    if stress is not None:
        return 0 if stress.passed == stress.round else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
