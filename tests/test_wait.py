"""Waiting for the UI to change (keyhac/core/wait.py).

Hermetic on every platform: with no event loop wired, conditions evaluate
inline, which is the documented library/test behaviour of
keymap.call_on_main_thread.
"""

import threading
import time

import pytest

from keyhac.core.wait import (
    MAX_INTERVAL, WaitTimeout, evaluate_on_main_thread, wait_for,
    wait_for_element, wait_for_stable, wait_until_gone,
)


class Fake:
    """An element whose subtree the test can change mid-wait."""

    def __init__(self, role=None, name=None, value=None, identifier=None,
                 children=(), key=None):
        self._describe = {"role": role, "name": name, "value": value,
                          "identifier": identifier, "rect": None}
        self.kids = list(children)
        self._key = key or id(self)

    def describe(self):
        return dict(self._describe)

    def children(self):
        return list(self.kids)

    def identity_key(self):
        return self._key


# -- wait_for ---------------------------------------------------------------

def test_returns_what_the_condition_returned():
    assert wait_for(lambda: "ready") == "ready"


def test_waits_until_true_then_returns():
    calls = []

    def condition():
        calls.append(time.monotonic())
        return len(calls) >= 3

    assert wait_for(condition, timeout=2) is True
    assert len(calls) == 3


def test_timeout_raises_with_the_message():
    started = time.monotonic()
    with pytest.raises(WaitTimeout, match="the modal to open"):
        wait_for(lambda: False, timeout=0.3, message="the modal to open")
    assert 0.3 <= time.monotonic() - started < 2.0


def test_timeout_is_a_timeout_error():
    """So an action can catch either name."""
    assert issubclass(WaitTimeout, TimeoutError)


def test_falsy_results_keep_waiting_but_zero_is_not_a_hang():
    """A condition returning 0 is "not yet" - the value must be truthy."""
    results = [0, "", None, "found"]
    assert wait_for(lambda: results.pop(0), timeout=2) == "found"


def test_polling_backs_off():
    """Otherwise a ten-minute wait is a hot loop against another process."""
    stamps = []
    with pytest.raises(WaitTimeout):
        wait_for(lambda: stamps.append(time.monotonic()), timeout=1.2)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    # Not gaps[-1]: the last one is clipped to whatever is left of the timeout,
    # so the wait ends on the deadline rather than overshooting it.
    assert gaps[0] < max(gaps), "intervals should grow"
    assert max(gaps) <= MAX_INTERVAL * 1.5


def test_fixed_interval_when_asked():
    stamps = []
    with pytest.raises(WaitTimeout):
        wait_for(lambda: stamps.append(time.monotonic()), timeout=0.5, interval=0.1)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g < 0.2 for g in gaps)


def test_wake_event_shortens_the_gap():
    """The accelerator: a notification wakes the wait instead of the timer."""
    wake = threading.Event()
    ready = []

    def condition():
        return bool(ready)

    def notify():
        time.sleep(0.05)
        ready.append(True)
        wake.set()

    threading.Thread(target=notify, daemon=True).start()
    started = time.monotonic()
    # A long fixed interval: without the wake this could not return quickly.
    wait_for(condition, timeout=5, wake=wake, interval=2.0)
    assert time.monotonic() - started < 1.0


# -- element waits ----------------------------------------------------------

def test_wait_for_element_returns_the_node():
    root = Fake("Window", key="w")

    def appear():
        time.sleep(0.05)
        root.kids.append(Fake("AXButton", name="Save", identifier="save", key="s"))

    threading.Thread(target=appear, daemon=True).start()
    node = wait_for_element(root, identifier="save", timeout=2)
    assert node.name == "Save"


def test_wait_for_element_times_out_naming_the_criteria():
    root = Fake("Window", key="w")
    with pytest.raises(WaitTimeout, match="identifier='nope'"):
        wait_for_element(root, identifier="nope", timeout=0.2)


def test_wait_until_gone():
    child = Fake("AXSheet", identifier="modal", key="m")
    root = Fake("Window", children=[child], key="w")

    def dismiss():
        time.sleep(0.05)
        root.kids.clear()

    threading.Thread(target=dismiss, daemon=True).start()
    wait_until_gone(root, identifier="modal", timeout=2)
    assert root.kids == []


def test_wait_until_gone_times_out_while_it_is_still_there():
    root = Fake("Window", children=[Fake("AXSheet", identifier="modal", key="m")],
                key="w")
    with pytest.raises(WaitTimeout):
        wait_until_gone(root, identifier="modal", timeout=0.2)


# -- stability --------------------------------------------------------------

def test_wait_for_stable_returns_once_the_tree_settles():
    root = Fake("Window", key="w")

    def churn():
        for i in range(3):
            root.kids.append(Fake("Row", value=f"r{i}", key=f"r{i}"))
            time.sleep(0.05)

    threading.Thread(target=churn, daemon=True).start()
    started = time.monotonic()
    wait_for_stable(root, quiet=0.2, timeout=5)
    assert time.monotonic() - started >= 0.2
    assert len(root.kids) == 3


def test_wait_for_stable_times_out_on_a_tree_that_never_settles():
    root = Fake("Window", key="w")
    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set():
            root.kids = [Fake("Row", value=f"r{i}", key=f"r{i}")]
            i += 1
            time.sleep(0.02)

    thread = threading.Thread(target=churn, daemon=True)
    thread.start()
    try:
        with pytest.raises(WaitTimeout, match="stop changing"):
            wait_for_stable(root, quiet=0.3, timeout=0.8)
    finally:
        stop.set()
        thread.join(timeout=2)


# -- main-thread dispatch ---------------------------------------------------

def test_condition_errors_propagate_rather_than_reading_as_false():
    def condition():
        raise ValueError("the tree read failed")

    with pytest.raises(ValueError, match="the tree read failed"):
        wait_for(condition, timeout=1)


def test_evaluate_runs_inline_without_a_loop():
    assert evaluate_on_main_thread(lambda: threading.current_thread()) is \
        threading.current_thread()


def test_refuses_to_block_the_loop_thread(engine, monkeypatch):
    """Waiting on the loop thread would hang the keyboard hook (§13)."""
    fixture = engine(lambda keymap: None)
    # A dispatcher that never runs anything: if the guard fails to fire, the
    # test hangs rather than passing by accident.
    fixture.keymap.set_main_thread_dispatcher(lambda callback: None)
    assert threading.current_thread() is threading.main_thread()
    with pytest.raises(RuntimeError, match="event-loop thread"):
        wait_for(lambda: False, timeout=0.2)


def test_worker_thread_dispatches_to_the_loop(engine):
    """The normal path: run() waits, the condition runs on the loop thread."""
    fixture = engine(lambda keymap: None)
    ran_on = []

    def dispatcher(callback):
        # Stand in for the event loop: run it on a dedicated "loop" thread.
        thread = threading.Thread(target=callback, name="loop")
        thread.start()
        thread.join()

    fixture.keymap.set_main_thread_dispatcher(dispatcher)
    result = {}

    def worker():
        result["value"] = wait_for(
            lambda: ran_on.append(threading.current_thread().name) or "done",
            timeout=2)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert result["value"] == "done"
    assert ran_on == ["loop"]
