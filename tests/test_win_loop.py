"""Windows event loop main-thread dispatch (keyhac/platform/win/loop.py).

Two halves, tested separately because only one of them needs Windows:

- the queue semantics (_drain_pending) are plain Python and run everywhere,
  driven on an instance built with __new__ - WinEventLoop.__init__ refuses to
  run off-Windows, and the ctypes surface is not what these pin down;
- the pump itself (PostThreadMessageW -> WM_APP+1 -> drain) needs a real
  message queue and is Windows-only, mirroring tests/test_mac_loop.py.
"""

import sys
import threading
import time

import pytest

from keyhac.platform.win.loop import WinEventLoop


def _detached_loop():
    """A WinEventLoop with just the queue state, no Windows message queue."""
    loop = WinEventLoop.__new__(WinEventLoop)
    loop._pending_lock = threading.Lock()
    loop._pending = []
    return loop


class TestDrainSemantics:

    def test_callbacks_drain_in_order(self):
        loop = _detached_loop()
        order = []
        for i in range(5):
            loop._pending.append(lambda i=i: order.append(i))
        loop._drain_pending()
        assert order == [0, 1, 2, 3, 4]
        assert loop._pending == []

    def test_a_callback_that_posts_another_does_not_extend_this_batch(self):
        # The re-entrant case: _drain_pending holds no lock while calling, and
        # the newly queued callback must wait for its own WM_APP+1 rather than
        # being appended to the list currently being walked (which would also
        # let a self-reposting callback spin forever).
        loop = _detached_loop()
        ran = []

        def first():
            ran.append("first")
            loop._pending.append(lambda: ran.append("second"))

        loop._pending.append(first)
        loop._drain_pending()
        assert ran == ["first"]
        assert len(loop._pending) == 1

        loop._drain_pending()
        assert ran == ["first", "second"]

    def test_a_raising_callback_does_not_drop_the_rest(self):
        loop = _detached_loop()
        ran = []

        def boom():
            raise RuntimeError("callback bug")

        loop._pending.extend([boom, lambda: ran.append("after")])
        loop._drain_pending()  # logged, not raised
        assert ran == ["after"]


@pytest.mark.skipif(sys.platform != "win32", reason="needs a real message queue")
class TestCallOnMainThread:

    WATCHDOG = 5.0

    def test_worker_callback_runs_on_the_loop_thread(self):
        loop = WinEventLoop()
        main_thread = threading.get_ident()
        seen = {}

        def worker():
            time.sleep(0.2)  # let the pump block in GetMessageW first
            seen["posted_from"] = threading.get_ident()

            def callback():
                seen["ran_on"] = threading.get_ident()
                loop.stop()

            loop.call_on_main_thread(callback)

        def watchdog():
            time.sleep(self.WATCHDOG)
            seen["timed_out"] = True
            loop.stop()

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=watchdog, daemon=True).start()
        loop.run()

        assert not seen.get("timed_out"), "callback never ran"
        assert seen["ran_on"] == main_thread
        assert seen["posted_from"] != main_thread
