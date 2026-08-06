"""macOS event loop main-thread dispatch (keyhac/platform/mac/loop.py) - live.

Runs a real CFRunLoop on the test's own thread and hands work to it from a
worker, which is the only way to prove the two halves that matter: that the
block is delivered to the *loop's* thread rather than the caller's, and that
CFRunLoopWakeUp actually rouses a loop parked with nothing else to do. A
watchdog stops the loop so a regression fails instead of hanging the suite.
"""

import sys
import threading
import time

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS-only platform layer", allow_module_level=True)

import Quartz  # noqa: E402

from keyhac.platform.mac.loop import MacEventLoop  # noqa: E402

WATCHDOG = 5.0


def _run_with_watchdog(loop):
    """Run the loop until something stops it; returns True if it timed out."""
    timed_out = []

    def watchdog():
        time.sleep(WATCHDOG)
        timed_out.append(True)
        Quartz.CFRunLoopStop(Quartz.CFRunLoopGetMain())

    threading.Thread(target=watchdog, daemon=True).start()
    loop.run()
    return bool(timed_out)


class TestCallOnMainThread:

    def test_worker_callback_runs_on_the_loop_thread(self):
        loop = MacEventLoop()
        main_thread = threading.get_ident()
        seen = {}

        def worker():
            # Let the loop park first, so the wake-up is what delivers this.
            time.sleep(0.2)
            seen["posted_from"] = threading.get_ident()

            def callback():
                seen["ran_on"] = threading.get_ident()
                loop.stop()

            loop.call_on_main_thread(callback)

        threading.Thread(target=worker, daemon=True).start()

        assert not _run_with_watchdog(loop), "callback never ran"
        assert seen["ran_on"] == main_thread
        assert seen["posted_from"] != main_thread

    def test_callbacks_run_in_order_and_release_their_references(self):
        loop = MacEventLoop()
        order = []

        def worker():
            time.sleep(0.2)
            for i in range(5):
                loop.call_on_main_thread(lambda i=i: order.append(i))
            loop.call_on_main_thread(loop.stop)

        threading.Thread(target=worker, daemon=True).start()

        assert not _run_with_watchdog(loop), "callbacks never ran"
        assert order == [0, 1, 2, 3, 4]
        # Each block drops itself from the keep-alive set once it has run;
        # a leak here would grow for the life of the process.
        assert loop._blocks == set()

    def test_callback_from_the_loop_thread_itself_is_still_delivered(self):
        # ThreadedAction.finished() may already be on the main thread (the
        # future can complete before add_done_callback attaches); dispatching
        # to yourself has to work rather than deadlock or drop.
        loop = MacEventLoop()
        ran = []

        def first():
            loop.call_on_main_thread(second)

        def second():
            ran.append("second")
            loop.stop()

        loop.call_on_main_thread(first)
        assert not _run_with_watchdog(loop), "nested callback never ran"
        assert ran == ["second"]
