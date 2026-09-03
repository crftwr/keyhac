"""ClipboardHistory with a fake provider, and the clipboard's thread rule."""

import threading
import time

import pytest

from keyhac.core.clipboard_history import ClipboardHistory
from keyhac.core.keymap import Keymap
from keyhac.platform.base import ClipboardProvider, main_thread_only


class FakeProvider(ClipboardProvider):
    def __init__(self):
        self.text = None
        self.changed = False

    def get_text(self):
        return self.text

    def set_text(self, s):
        self.text = s

    def poll(self):
        changed, self.changed = self.changed, False
        return changed


def make(tmp_path):
    return FakeProvider(), str(tmp_path / "clipboard.json")


class TestClipboardHistory:

    def test_capture_and_order(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        for text in ("one", "two", "three"):
            provider.text = text
            history.on_clipboard_changed()
        assert [s for s, _ in history.items()] == ["three", "two", "one"]
        assert history.get_current() == "three"

    def test_duplicates_move_to_front(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        for text in ("a", "b", "a"):
            history.add_item(text)
        assert [s for s, _ in history.items()] == ["a", "b"]

    def test_set_current_updates_os_clipboard(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        history.set_current("hello")
        assert provider.text == "hello"
        assert history.get_current() == "hello"

    def test_persistence_round_trip(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        history.add_item("first")
        history.add_item("second")
        assert history.dirty
        history.flush()
        assert not history.dirty

        reloaded = ClipboardHistory(FakeProvider(), path)
        assert [s for s, _ in reloaded.items()] == ["second", "first"]

    def test_max_items_cap(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        old_cap = ClipboardHistory.max_items
        ClipboardHistory.max_items = 3
        try:
            for i in range(5):
                history.add_item(f"item{i}")
            assert len(list(history.items())) == 3
            assert history.get_current() == "item4"
        finally:
            ClipboardHistory.max_items = old_cap

    def test_labels_are_flattened(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        history.add_item("line one\n  line two\t\tend")
        _s, label = next(history.items())
        assert label == "line one line two end"


class OffMainThread(ClipboardProvider):
    """A provider that records which thread its calls actually ran on."""

    def __init__(self):
        self.ran_on = []

    @main_thread_only
    def get_text(self):
        self.ran_on.append(threading.current_thread())
        return "read"

    @main_thread_only
    def set_text(self, s):
        self.ran_on.append(threading.current_thread())

    @main_thread_only
    def poll(self):
        self.ran_on.append(threading.current_thread())
        return False


class TestMainThreadOnly:
    """The clipboard is reached from an action's worker, and NSPasteboard
    segfaults there - `EXC_BAD_ACCESS` in `-[NSPasteboard stringForType:]`,
    measured, with the process gone and the keyboard hook with it."""

    @pytest.fixture(autouse=True)
    def no_leftover_dispatcher(self):
        """Keymap is a singleton, so a dispatcher wired here is still wired for
        the next test - the hazard test_wait.py records."""
        yield
        keymap = Keymap.get_instance()
        if keymap is not None:
            keymap.set_main_thread_dispatcher(None)

    def _queueing(self, engine):
        """An engine whose dispatcher queues work for the main thread.

        Not `lambda callback: callback()`: that runs the work on whichever
        thread handed it over, which is the very thing being tested against.
        """
        made = engine(lambda keymap: None)
        queued = []
        made.keymap.set_main_thread_dispatcher(queued.append)
        return queued

    def _run_on_main_until_done(self, queued, worker, timeout=5.0):
        """Be the event loop: drain the queue here while the worker waits."""
        deadline = time.monotonic() + timeout
        while worker.is_alive() and time.monotonic() < deadline:
            while queued:
                queued.pop(0)()
            time.sleep(0.005)
        worker.join(timeout=1.0)
        assert not worker.is_alive(), "the worker never came back"

    def test_a_worker_thread_call_runs_on_the_loop_thread(self, engine):
        queued = self._queueing(engine)
        provider = OffMainThread()

        worker = threading.Thread(target=provider.get_text)
        worker.start()
        self._run_on_main_until_done(queued, worker)

        assert provider.ran_on == [threading.main_thread()]

    def test_every_method_is_covered(self, engine):
        queued = self._queueing(engine)
        provider = OffMainThread()

        def use_it():
            provider.get_text()
            provider.set_text("x")
            provider.poll()

        worker = threading.Thread(target=use_it)
        worker.start()
        self._run_on_main_until_done(queued, worker)

        assert provider.ran_on == [threading.main_thread()] * 3

    def test_the_value_and_the_arguments_survive_the_trip(self, engine):
        queued = self._queueing(engine)
        provider = OffMainThread()
        seen = []

        worker = threading.Thread(target=lambda: seen.append(provider.get_text()))
        worker.start()
        self._run_on_main_until_done(queued, worker)

        assert seen == ["read"]

    def test_on_the_loop_thread_it_runs_inline(self, engine):
        # The history polls from the loop thread every tick, and a dispatch to
        # ourselves followed by a wait would deadlock. Nothing is queued.
        queued = self._queueing(engine)

        provider = OffMainThread()
        provider.poll()

        assert provider.ran_on == [threading.main_thread()]
        assert queued == []
