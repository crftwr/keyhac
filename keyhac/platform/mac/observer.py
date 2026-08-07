"""AX notification subscription - the macOS half of Layer 1's required piece.

Nothing in Keyhac subscribed to accessibility notifications before this: the
focus provider polls, and macro recording captures input rather than outcome.
What subscription buys is latency.  `keyhac.core.wait` is correct on polling
alone, but a poll that has backed off to a quarter second notices a modal a
quarter second late, and an action that opens two hundred modals pays that
delay two hundred times.  Handing its `wake` event to one of these collapses
the delay to the notification's own.

It is an accelerator and never a dependency - which is what lets the output
side work identically on Windows before its WinEvent/UIA counterpart exists.

THREADING.  An AXObserver delivers on whichever run loop its source was added
to, and the only loop Keyhac is sure is turning is the main one.  So creation
and teardown hop to the main thread, and the callback - which runs there -
does nothing but set a threading.Event.  The waiting worker does the work.

DELIVERY IS BEST EFFORT.  AX notifications do not bubble: registering on an
application element gets the application-level ones (a window appearing, focus
moving), not a value change deep in its subtree, for which you must register on
that element.  Applications also differ in which they bother to post.  Since a
missed notification only costs the polling delay it was avoiding, this
registers what it can and ignores what an app refuses.
"""

from __future__ import annotations

import threading

import ApplicationServices as AS
import objc
import Quartz

from keyhac.core import log

logger = log.getLogger("MacObserver")

#: AXObserverRef -> the UIObserver that created it.
#:
#: PyObjC will not take a plain Python callable for an AX callback ("Callable
#: argument is not a PyObjC closure"); it has to be a closure built by
#: objc.callbackFor, which is a module-level object with no per-instance state.
#: So delivery arrives here and is routed by the observer it came from.  The
#: refs hash by CFEqual, the same property the tree walk's dedupe relies on.
_OBSERVERS: dict = {}


@objc.callbackFor(AS.AXObserverCreate)
def _deliver(observer, element, notification, refcon):
    """The C callback.  Runs on the main run loop - keep it to a doorbell."""
    watcher = _OBSERVERS.get(observer)
    if watcher is not None:
        watcher._on_notification(str(notification))

#: What "something happened" is taken to mean, when the caller does not say.
#: Application-level by design - see the module docstring on bubbling.
DEFAULT_NOTIFICATIONS = (
    "AXWindowCreated",
    "AXUIElementDestroyed",
    "AXFocusedUIElementChanged",
    "AXFocusedWindowChanged",
    "AXMainWindowChanged",
    "AXWindowMiniaturized",
    "AXWindowDeminiaturized",
    "AXValueChanged",
    "AXTitleChanged",
    # Emitted by WebKit and Chromium when a page re-lays-out; the closest thing
    # to "the document changed" that web content offers.
    "AXLayoutChanged",
    "AXLiveRegionChanged",
    "AXMenuOpened",
    "AXMenuClosed",
    "AXCreated",
)


class UIObserver:
    """Sets an event whenever a watched application posts a notification.

    ```python
    with UIObserver(pid) as observer:
        wait_for_element(window, name="Save", wake=observer.event)
    ```

    Attributes:
        event: A threading.Event, set on every delivered notification.  Waiters
            clear it themselves; it is a doorbell, not a queue.
        last: Name of the most recent notification, for diagnosis.
        count: How many have arrived.
    """

    def __init__(self, pid: int, element=None,
                 notifications=DEFAULT_NOTIFICATIONS):
        """Subscribe to `notifications` for the application `pid`.

        Args:
            pid: Process id of the application to watch.
            element: Element to register on; defaults to the application
                element, which is what application-level notifications are
                posted for.
            notifications: Notification names to ask for.  Ones the application
                rejects are skipped with a debug log, not an error.
        """
        self.pid = int(pid)
        self.event = threading.Event()
        self.notifications: list[str] = []
        self.last: str | None = None
        self.count = 0
        self._observer = None
        self._element = element._ref if hasattr(element, "_ref") else element
        if self._element is None:
            self._element = AS.AXUIElementCreateApplication(self.pid)
        self._install(notifications)

    # -- lifecycle ----------------------------------------------------------

    def _install(self, notifications) -> None:
        from keyhac.core.wait import evaluate_on_main_thread

        def install():
            err, observer = AS.AXObserverCreate(self.pid, _deliver, None)
            if err != 0 or observer is None:
                logger.debug(f"AXObserverCreate failed for pid {self.pid}: {err}")
                return None
            added = []
            for name in notifications:
                err = AS.AXObserverAddNotification(observer, self._element, name, None)
                if err == 0:
                    added.append(name)
                else:
                    logger.debug(f"pid {self.pid} refused {name}: {err}")
            _OBSERVERS[observer] = self
            Quartz.CFRunLoopAddSource(
                Quartz.CFRunLoopGetMain(),
                AS.AXObserverGetRunLoopSource(observer),
                Quartz.kCFRunLoopCommonModes)
            return observer, added

        result = evaluate_on_main_thread(install)
        if result is not None:
            self._observer, self.notifications = result

    def close(self) -> None:
        """Unsubscribe.  Idempotent."""
        from keyhac.core.wait import evaluate_on_main_thread

        observer, self._observer = self._observer, None
        if observer is None:
            return

        def remove():
            _OBSERVERS.pop(observer, None)
            for name in self.notifications:
                AS.AXObserverRemoveNotification(observer, self._element, name)
            Quartz.CFRunLoopRemoveSource(
                Quartz.CFRunLoopGetMain(),
                AS.AXObserverGetRunLoopSource(observer),
                Quartz.kCFRunLoopCommonModes)

        evaluate_on_main_thread(remove)

    def __enter__(self) -> "UIObserver":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- delivery -----------------------------------------------------------

    def _on_notification(self, name: str) -> None:
        # Runs on the main run loop.  Do as little as possible here: the
        # keyboard hook shares this thread.
        self.last = name
        self.count += 1
        self.event.set()

    @property
    def active(self) -> bool:
        """Whether the subscription is live (False when the app refused)."""
        return self._observer is not None
