"""Cancelling a running action with Esc (Layer 3).

What is pinned here is the set of things that fail *silently* if they regress:
a cancellation swallowed by an action's own `except Exception`, an Esc that
Keyhac itself injected killing the action that injected it, and the clipboard
save/restore racing now that more than one action can run at a time.
"""

import threading
import time

import pytest

from keyhac.core.action import ActionCancelled, ThreadedAction, current_action
from keyhac.core.wait import wait_for
from keyhac.platform.base import KeyEvent


# -- the exception's one job -------------------------------------------------

def test_cancellation_survives_the_handler_every_action_writes():
    """`extract_records` wraps each system in `except Exception` so one bad
    system does not lose the others - the shape this whole class of action has.
    An ordinary exception would be recorded there as "SystemA failed" and the
    run would carry on to SystemB, which is precisely what cancelling must not
    do."""
    reached_second_system = False

    def run():
        nonlocal reached_second_system
        for index in (0, 1):
            try:
                if index == 0:
                    raise ActionCancelled("user pressed Esc")
            except Exception:                             # noqa: BLE001
                continue                                  # "record and go on"
            reached_second_system = True

    with pytest.raises(ActionCancelled):
        run()
    assert not reached_second_system, "the cancellation was swallowed"


def test_it_is_not_an_exception():
    assert issubclass(ActionCancelled, BaseException)
    assert not issubclass(ActionCancelled, Exception)


# -- wait_for is where an action notices -------------------------------------

class Waiter(ThreadedAction):
    """Waits forever, and records how it came out."""

    def __init__(self):
        self.outcome = None
        self.cleaned_up = False
        self.started = threading.Event()

    def run(self):
        try:
            self.started.set()
            wait_for(lambda: False, timeout=30, message="something",
                     interval=0.01)
        except ActionCancelled:
            self.outcome = "cancelled"
            raise
        finally:
            # The reason cancellation unwinds rather than kills the thread:
            # progress already made has to be written down (§2.1).
            self.cleaned_up = True


def test_wait_for_raises_when_the_action_is_cancelled():
    action = Waiter()

    # cancellable() is entered *on the thread that will run run()*, because
    # that is where it plants the thread-local wait_for consults. Both real
    # callers do exactly this - _run_tracked is what the pool submits, and the
    # MCP tool wraps its own run() call - so a test that enters it elsewhere
    # is testing a shape nothing uses.
    def worker():
        with action.cancellable():
            _swallow(action.run)

    thread = threading.Thread(target=worker)
    thread.start()
    # run() sets this after cancellable() has registered, so waiting on it
    # removes the race with cancel_all below.
    assert action.started.wait(5), "run() never started"
    ThreadedAction.cancel_all()
    thread.join(10)

    assert not thread.is_alive(), "wait_for did not come back"
    assert action.outcome == "cancelled"
    assert action.cleaned_up, "finally did not run - progress would be lost"


def _swallow(func):
    try:
        func()
    except BaseException:                                 # noqa: BLE001
        pass


def test_wait_for_is_unaffected_outside_an_action():
    """A bare wait_for - a test, a library caller - has no action to consult,
    and must not trip over the thread-local being empty."""
    assert current_action() is None
    assert wait_for(lambda: "ready", timeout=1) == "ready"


# -- the flag ----------------------------------------------------------------

def test_cancel_all_reaches_only_running_actions():
    action = Waiter()
    assert action.cancelled() is False
    assert ThreadedAction.cancel_all() == 0, "nothing is running"

    with action.cancellable():
        assert ThreadedAction.cancel_all() == 1
        assert action.cancelled() is True

    # Out of the block it is deregistered, so a later Esc does not find it.
    assert ThreadedAction.cancel_all() == 0


def test_check_cancelled_covers_a_stretch_with_no_wait_in_it():
    action = Waiter()
    with action.cancellable():
        action.check_cancelled()                          # no-op while running
        ThreadedAction.cancel_all()
        with pytest.raises(ActionCancelled):
            action.check_cancelled()


# -- the key event -----------------------------------------------------------

class Counter(ThreadedAction):
    def run(self):
        return None


def _escape(engine):
    from keyhac.core.vk import get_key_names
    return get_key_names().str_to_vk("Escape")


def test_only_physical_escape_cancels(engine):
    """The requirement that made this worth a test: Keyhac injects Esc itself,
    and an action that presses Escape to dismiss a dialog must not thereby kill
    itself. Translated output never reaches on_key_event at all (the platform
    layer drops it on its own tag), so what is checked here is the other half -
    that replay-injected input is excluded too, since a macro replaying an Esc
    is not a user asking to stop."""
    e = engine(lambda keymap: None)
    action = Waiter()
    vk = _escape(e)

    with action.cancellable():
        assert e.keymap.on_key_event(KeyEvent(vk, True, "replay")) is not True
        assert action.cancelled() is False, "a replayed Esc cancelled an action"

        assert e.keymap.on_key_event(KeyEvent(vk, True, "real")) is True
        assert action.cancelled() is True


def test_escape_passes_through_when_nothing_is_running(engine):
    """Swallowing every Esc would change what the focused application sees."""
    e = engine(lambda keymap: None)
    assert e.keymap.on_key_event(KeyEvent(_escape(e), True, "real")) is not True


def test_escape_up_is_not_a_cancel(engine):
    e = engine(lambda keymap: None)
    action = Waiter()
    with action.cancellable():
        e.keymap.on_key_event(KeyEvent(_escape(e), False, "real"))
        assert action.cancelled() is False


# -- what raising the pool exposed -------------------------------------------

def test_the_pool_no_longer_serializes_everything():
    """The §2.1 bug: one long run() held the single worker, so an unrelated key
    binding did nothing until it returned."""
    assert ThreadedAction.thread_pool._max_workers > 1

    started = threading.Barrier(2, timeout=10)

    def occupy():
        started.wait()
        time.sleep(0.05)

    first = ThreadedAction.thread_pool.submit(occupy)
    second = ThreadedAction.thread_pool.submit(occupy)
    # Would deadlock on the timeout with one worker: the second never starts.
    first.result(timeout=10)
    second.result(timeout=10)


def test_preserve_clipboard_is_reentrant():
    """`_paste` opens the context inside a caller that already opened it, which
    is the documented way to write several fields. A plain Lock would deadlock
    the example in preserve_clipboard's own docstring."""
    from keyhac.core.fill import preserve_clipboard

    with preserve_clipboard():
        with preserve_clipboard():
            pass


def test_preserve_clipboard_serializes_across_threads():
    """With one pool worker this was free. With several, two actions pasting at
    once would each save the other's scratch value and put it back as "what the
    user had"."""
    from keyhac.core.fill import preserve_clipboard

    overlaps = []
    inside = threading.Lock()
    depth = [0]

    def paste():
        with preserve_clipboard():
            with inside:
                depth[0] += 1
                overlaps.append(depth[0])
            time.sleep(0.02)
            with inside:
                depth[0] -= 1

    threads = [threading.Thread(target=paste) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert max(overlaps) == 1, f"two threads were inside at once: {overlaps}"


# -- staleness ---------------------------------------------------------------
#
# A UINode is a snapshot (uitree.StaleElement documents why). These pin the
# distinction that the contract is worth having for: "the screen moved" and
# "your selector is wrong" used to arrive as the same message.

class DeadElement:
    """An element that has gone away, the way a closed dialog's button has."""
    def is_stale(self):
        return True
    def get_action_names(self):
        return []                       # what a dead element reports


class LiveButNotPressable:
    """A real element that simply offers no press action."""
    def is_stale(self):
        return False
    def get_action_names(self):
        return ["AXShowMenu"]


class Ancient:
    """A duck-typed element from before is_stale existed."""
    def get_action_names(self):
        return []


def test_a_vanished_element_says_so_rather_than_blaming_the_selector():
    from keyhac.core.fill import _press
    from keyhac.core.uitree import StaleElement

    with pytest.raises(StaleElement) as caught:
        _press(DeadElement())
    assert "no longer on screen" in str(caught.value)


def test_a_live_element_with_no_press_action_still_reports_that():
    from keyhac.core.fill import FillFailed, _press

    with pytest.raises(FillFailed) as caught:
        _press(LiveButNotPressable())
    assert "supports no press action" in str(caught.value)


def test_staleness_is_an_ordinary_exception():
    """Unlike ActionCancelled: an action that wants to re-find the element and
    carry on should be able to catch this in the handler it already has."""
    from keyhac.core.uitree import StaleElement
    assert issubclass(StaleElement, Exception)


def test_an_element_without_is_stale_is_unaffected():
    """register_action and the tests both accept duck-typed elements."""
    from keyhac.core.fill import FillFailed, _press

    with pytest.raises(FillFailed):
        _press(Ancient())
