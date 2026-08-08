"""Waiting for the UI to change - the primitive `sleep` is standing in for.

Nearly every step of an action that *acts* on the UI is "do something, then
wait for something to change": a modal opens, a modal closes, a page loads
after a pagination click, a dependent field re-renders, an application becomes
ready.  Spelling that as `sleep(2)` produces the classic
works-on-my-machine failure - it passes on the developer's machine and fails on
a slower or a faster one, and the faster one fails *silently*, acting on a
screen that has not arrived yet.

So: `sleep` in a generated action is a defect, and these are what replace it
(doc/dev/ai-integration.md §7.1).

HOW THE WAIT IS SPENT.  A condition that reads UI elements has to run on the
thread that owns the event loop, which is not the thread calling wait_for -
the whole point is that the caller is a ThreadedAction.run() worker, because a
key press must return control immediately (§13).  Each poll therefore hands the
condition to keymap.call_on_main_thread and blocks for its answer.  Polling
starts fast and backs off: a modal that opens in 30 ms is caught in 30 ms,
while a ten-minute job costs a check every quarter second rather than a
thousand tree walks a minute.

There is deliberately no event-subscription path.  One existed - an AXObserver
wrapper feeding a wake event into the loop below - and it was removed once
measured: native Cocoa applications post notifications generously, but WebKit
*and* Chromium content post nothing at all for a change inside the page, which
is where this workload lives.  Even for native targets the win was small, and
in the direction that does not matter: the first poll is 20 ms, so a fast
transition is caught fast anyway, and a wait long enough to have backed off to
250 ms is a wait where 250 ms of latency is noise.  See
doc/dev/ai-integration.md §5 and the skill's references/quirks.md.

NOT SOLVED HERE: a long wait occupies ThreadedAction's single pool worker for
its whole duration, so a ten-minute wait stalls every other threaded action in
the app.  That is the executor problem in §2.1 of the design document, it
predates this module, and it wants its own fix.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from keyhac.core import log
from keyhac.core.action import ActionCancelled, current_action
from keyhac.core.uitree import UINode, find_element, get_ui_tree

logger = log.getLogger("Wait")

#: How long to wait before giving up, when the caller does not say.
DEFAULT_TIMEOUT = 10.0

#: First polling gap.  Short, because most UI transitions are fast and the
#: cost of noticing late is paid by every step of every iteration.
MIN_INTERVAL = 0.02

#: Longest polling gap, reached by backing off.
MAX_INTERVAL = 0.25

#: Backoff factor between the two.
BACKOFF = 1.5

#: How long a tree must stop changing before wait_for_stable calls it settled.
DEFAULT_QUIET = 0.3


class WaitTimeout(TimeoutError):
    """A wait gave up.

    Deliberately its own type, and deliberately an error rather than a False
    return: an action whose precondition never arrived must stop, not carry on
    against a screen that is not there (design document §3.7).
    """


def on_loop_thread() -> bool:
    """Whether this thread is the one turning the event loop.

    False when no loop is wired at all (library use, tests), because then
    nothing can be blocked by running work inline.
    """
    from keyhac.core.keymap import Keymap

    keymap = Keymap.get_instance()
    return (keymap is not None
            and keymap._main_thread_dispatcher is not None
            and threading.current_thread() is threading.main_thread())


def evaluate_on_main_thread(func: Callable[[], Any], timeout: float = 5.0) -> Any:
    """Run `func` on the event-loop thread and return what it returned.

    The supported way to read elements from a worker.  Exceptions raised inside
    `func` are re-raised here, so a condition that throws is not silently a
    False.

    Runs inline in two cases: when there is no loop to hand work to (Keyhac
    used as a library, or under test), and when the caller is *already* on the
    loop thread.  The second matters more than it looks - a helper that reads
    an element gets called both from a worker and from inside another
    condition that is already running on the loop, and it must work in both
    places.  Dispatching to ourselves and then waiting would deadlock, and
    refusing outright would make such helpers uncomposable.
    """
    from keyhac.core.keymap import Keymap

    keymap = Keymap.get_instance()
    if keymap is None or keymap._main_thread_dispatcher is None:
        return func()
    if threading.current_thread() is threading.main_thread():
        return func()

    box: dict[str, Any] = {}
    done = threading.Event()

    def call():
        try:
            box["value"] = func()
        except BaseException as error:      # noqa: BLE001 - re-raised below
            box["error"] = error
        finally:
            done.set()

    keymap.call_on_main_thread(call)
    if not done.wait(timeout):
        raise WaitTimeout(
            f"the event loop did not run a UI read within {timeout}s "
            f"(is the main thread blocked?)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _refuse_to_block_the_loop(name: str) -> None:
    """Guard the blocking calls - not the reads.

    Reading an element on the loop thread is ordinary and cheap; *waiting*
    there is what would hold the keyboard hook for the length of the wait
    (§13).  Putting this check on evaluate_on_main_thread instead made every
    helper that reads an element unusable from inside a condition, which is
    where half of them get called from.
    """
    if on_loop_thread():
        raise RuntimeError(
            f"{name}() was called on the event-loop thread, which would block "
            f"the keyboard hook until it returned. Waiting belongs in "
            f"ThreadedAction.run(); see doc/configuration.md.")


def wait_for(condition: Callable[[], Any],
             timeout: float = DEFAULT_TIMEOUT,
             message: str | None = None,
             interval: float | None = None) -> Any:
    """Block until `condition()` returns something truthy, and return it.

    Args:
        condition: Called repeatedly on the event-loop thread; may read UI
            elements.  Keep it cheap - it runs many times, and a full tree walk
            is milliseconds each.
        timeout: Seconds before giving up.
        message: What was being waited for, used in the timeout error.  Worth
            writing: it is what an operator sees when an action stops.
        interval: Fixed polling gap.  The default backs off from MIN_INTERVAL
            to MAX_INTERVAL instead, which is nearly always what you want.

    Returns:
        Whatever `condition()` returned, so
        `element = wait_for(lambda: find_element(...))` is one step.

    Raises:
        WaitTimeout: The condition never became true.
        ActionCancelled: The user pressed Esc.  Nothing has to catch this -
            it unwinds through the action's `finally` blocks so progress
            already recorded stays recorded.
        RuntimeError: Called on the event-loop thread, where blocking would
            hang the keyboard hook.
    """
    _refuse_to_block_the_loop("wait_for")
    deadline = time.monotonic() + timeout
    gap = interval if interval is not None else MIN_INTERVAL

    while True:
        # Here, at the top, rather than anywhere else in the loop: this is
        # where control lands after each sleep, so cancelling costs at most
        # one polling gap and never a whole condition evaluation. Waiting is
        # where a long action spends nearly all of its time (§7.1), which is
        # what makes one check in one function enough to make Esc work
        # everywhere without an action containing a line about it.
        action = current_action()
        if action is not None and action.cancelled():
            raise ActionCancelled(
                f"cancelled while waiting for {message or 'a condition'}")

        result = evaluate_on_main_thread(condition)
        if result:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitTimeout(
                f"timed out after {timeout}s waiting for "
                f"{message or 'a condition'}")
        time.sleep(min(gap, remaining))
        if interval is None:
            gap = min(gap * BACKOFF, MAX_INTERVAL)


def wait_for_element(root, timeout: float = DEFAULT_TIMEOUT,
                     message: str | None = None, **criteria) -> UINode:
    """Wait until an element matching `criteria` exists, and return it.

    The first beat of the three that every menu and modal needs: wait for it to
    appear, act on it, then wait for it to go away before the next iteration
    starts (§7.2).  Takes the same criteria as `find_element`.

    Args:
        root: Element or UINode to search below.
        timeout: Seconds before giving up.
        message: What was being waited for, for the timeout error.  Defaults to
            the criteria, which names the element but not the step - say what
            the step was when an operator will read it.
        **criteria: As `find_element` - role, name, value, identifier, text,
            predicate.

    Raises:
        WaitTimeout: Nothing matched in time.
    """
    described = ", ".join(f"{k}={v!r}" for k, v in criteria.items())
    return wait_for(lambda: find_element(root, **criteria), timeout=timeout,
                    message=message or f"an element matching {described}")


def wait_until_gone(root, timeout: float = DEFAULT_TIMEOUT,
                    message: str | None = None, **criteria) -> None:
    """Wait until no element matches `criteria`.

    The third beat, and the one that actually breaks iteration when it is left
    out: without it the next cycle starts while the previous modal is still on
    screen, and clicks land in it.

    Takes the same arguments as `wait_for_element`.
    """
    described = ", ".join(f"{k}={v!r}" for k, v in criteria.items())
    wait_for(lambda: find_element(root, **criteria) is None, timeout=timeout,
             message=message or f"no element matching {described}")


def wait_for_stable(root, quiet: float = DEFAULT_QUIET,
                    timeout: float = DEFAULT_TIMEOUT,
                    max_depth: int | None = None,
                    max_nodes: int | None = None) -> None:
    """Wait until the subtree under `root` stops changing.

    For the re-render case, where nothing appears or disappears but the
    contents settle a beat after the click - a dependent field repopulating, a
    table repainting after a sort.

    Cost: one tree read per check, so bound it with `max_depth` / `max_nodes`
    when the subtree is large.  Prefer `wait_for_element` whenever there is a
    specific thing to wait for; this is the fallback for when there is not.

    Args:
        root: Element or UINode whose subtree should settle.
        quiet: Seconds of no change required.
        timeout: Seconds before giving up.
        max_depth: Depth bound for each read.
        max_nodes: Node bound for each read.

    Raises:
        WaitTimeout: The tree never stopped changing.
        RuntimeError: Called on the event-loop thread.
    """
    _refuse_to_block_the_loop("wait_for_stable")
    bounds = {}
    if max_depth is not None:
        bounds["max_depth"] = max_depth
    if max_nodes is not None:
        bounds["max_nodes"] = max_nodes

    def signature():
        tree = get_ui_tree(root, **bounds)
        # Roles and text, not identity: a repaint that replaces elements with
        # equivalent ones has not changed anything the action cares about.
        return tuple((n.depth, n.role, n.name, n.value) for n in tree.walk())

    deadline = time.monotonic() + timeout
    previous = evaluate_on_main_thread(signature)
    stable_since = time.monotonic()

    while True:
        time.sleep(min(MIN_INTERVAL * 2, max(deadline - time.monotonic(), 0) or 0.01))
        current = evaluate_on_main_thread(signature)
        now = time.monotonic()
        if current != previous:
            previous = current
            stable_since = now
        elif now - stable_since >= quiet:
            return
        if now >= deadline:
            raise WaitTimeout(
                f"timed out after {timeout}s waiting for the UI to stop "
                f"changing (never quiet for {quiet}s)")
