"""ThreadedAction's thread contract (keyhac/core/action.py).

starting() and finished() must both land on the event-loop thread, with only
run() on the pool worker - so a subclass can touch main-thread-only APIs
(UI, window moves, AX writes) in either callback without hopping by hand.
The dispatcher stands in for whichever loop is running: PuiKit's backend with
the console up, the platform EventLoop under --no-ui.
"""

import threading
import time

from keyhac.core.action import ThreadedAction


class RecordingAction(ThreadedAction):
    """Records the thread each callback ran on, and signals when done."""

    def __init__(self):
        self.threads = {}
        self.result = None
        self.done = threading.Event()

    def starting(self):
        self.threads["starting"] = threading.get_ident()

    def run(self):
        self.threads["run"] = threading.get_ident()
        return "payload"

    def finished(self, result):
        self.threads["finished"] = threading.get_ident()
        self.result = result
        self.done.set()


class QueueDispatcher:
    """A stand-in event loop: queues callbacks, runs them where drain() is
    called, exactly as a real loop runs them on its own thread."""

    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()

    def __call__(self, callback):
        with self.lock:
            self.queue.append(callback)

    def drain(self):
        with self.lock:
            callbacks, self.queue = self.queue, []
        for callback in callbacks:
            callback()
        return len(callbacks)


def _wait(action, timeout=5.0):
    assert action.done.wait(timeout), "finished() never ran"


class TestThreadDispatch:

    def test_finished_runs_on_the_dispatcher_not_the_pool_thread(self, engine):
        e = engine(lambda keymap: None)
        dispatcher = QueueDispatcher()
        e.keymap.set_main_thread_dispatcher(dispatcher)

        action = RecordingAction()
        action()

        # Nothing may reach finished() until the loop gets its turn.
        deadline = threading.Event()
        deadline.wait(0.3)
        assert not action.done.is_set()
        assert "run" in action.threads

        assert dispatcher.drain() == 1
        _wait(action)
        assert action.result == "payload"
        assert action.threads["finished"] == threading.get_ident()
        assert action.threads["run"] != threading.get_ident()

    def test_starting_and_finished_share_a_thread(self, engine):
        e = engine(lambda keymap: None)
        dispatcher = QueueDispatcher()
        e.keymap.set_main_thread_dispatcher(dispatcher)

        action = RecordingAction()
        action()
        deadline = time.monotonic() + 5.0
        while not dispatcher.drain():
            assert time.monotonic() < deadline, "nothing was ever dispatched"
        _wait(action)

        assert action.threads["starting"] == action.threads["finished"]
        assert action.threads["run"] != action.threads["starting"]

    def test_without_a_dispatcher_finished_still_runs(self, engine):
        # Keyhac as a library, or under test: no loop is wired, so the
        # callback runs inline on the pool thread as it always did.
        e = engine(lambda keymap: None)
        assert e.keymap._main_thread_dispatcher is None

        action = RecordingAction()
        action()
        _wait(action)
        assert action.result == "payload"
        assert action.threads["finished"] == action.threads["run"]

    def test_a_failing_run_is_logged_not_raised(self, engine):
        e = engine(lambda keymap: None)
        dispatcher = QueueDispatcher()
        e.keymap.set_main_thread_dispatcher(dispatcher)

        finished_called = []

        class Boom(ThreadedAction):
            def run(self):
                raise RuntimeError("action bug")

            def finished(self, result):
                finished_called.append(result)

        Boom()()
        deadline = threading.Event()
        deadline.wait(0.3)
        dispatcher.drain()  # the exception surfaces here, and is swallowed
        assert finished_called == []


class TestCallOnMainThread:

    def test_dispatcher_receives_the_callback(self, engine):
        e = engine(lambda keymap: None)
        dispatcher = QueueDispatcher()
        e.keymap.set_main_thread_dispatcher(dispatcher)

        ran = []
        e.keymap.call_on_main_thread(lambda: ran.append(1))
        assert ran == []        # queued, not run inline
        dispatcher.drain()
        assert ran == [1]

    def test_unwired_runs_inline(self, engine):
        e = engine(lambda keymap: None)
        ran = []
        e.keymap.call_on_main_thread(lambda: ran.append(1))
        assert ran == [1]
