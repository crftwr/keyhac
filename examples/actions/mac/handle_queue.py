"""Work a queue of confirmation dialogs - and refuse the one that is wrong.

The third hand-written action of doc/dev/ai-integration.md §10.  Queue
processing with per-item branching (§2) plus the three-beat modal cycle (§7.2),
but what it is really here to demonstrate is **preconditions** (§3.7):

    If the UI changed, an action stops and hands back to a human.  It does not
    guess, and it does not repair itself, because an action that silently does
    the wrong thing is worse than one that refuses to run.

The fixture makes that concrete.  After the real items are gone, the page shows
a *different* dialog - "Delete all records?" - with the same shape and a
first button that destroys everything.  A handler that presses "the first
button in the dialog", or that trusts the dialog it saw last time to be the
dialog on screen now, deletes the records and reports success.  This one checks
what it is looking at before every press, and stops.

Run it (macOS, Safari):

    python tools/run_action_file.py examples/actions/mac/handle_queue.py
"""

import pathlib
import subprocess

from keyhac import ThreadedAction, WaitTimeout, getLogger

logger = getLogger("HandleQueue")

FIXTURE = (pathlib.Path(__file__).resolve().parents[1]
           / "fixtures" / "dialog.html").as_uri()


class PreconditionFailed(RuntimeError):
    """The screen is not what this action was written against.

    Its own type so the caller can tell "the UI moved on me" from "the thing I
    was doing failed" - the first means regenerate the action, the second means
    retry it.
    """


class HandleQueue(ThreadedAction):
    """Approve every item in the queue, one dialog at a time."""

    #: What the dialog this action is allowed to touch must look like.  Both
    #: are checked before anything is pressed; either missing means stop.
    EXPECTED_TITLE = "Approve this item?"
    EXPECTED_BUTTON = "Approve"

    def __init__(self, app_name="Safari", url=FIXTURE, limit=10):
        self.app_name = app_name
        self.url = url
        self.limit = limit
        self.handled: list[str] = []
        self.stopped_because: str | None = None

    def starting(self):
        logger.info("working the approval queue")

    def run(self):
        subprocess.run(["open", "-a", self.app_name, self.url], check=True)
        window = self.ui.wait(lambda: self.ui.window(app=self.app_name),
                              timeout=20,
                              message=f"{self.app_name} to open a window")
        window.wait_for(identifier="next-item", timeout=20,
                        message="the queue page to load")

        for _ in range(self.limit):
            try:
                self._one_item(window)
            except PreconditionFailed as error:
                # Not an error in the run - it is the run correctly declining
                # to continue.  Everything already done stays done.
                self.stopped_because = str(error)
                break
            except WaitTimeout as error:
                self.stopped_because = f"gave up waiting: {error}"
                break
        return {"handled": self.handled, "stopped": self.stopped_because}

    def finished(self, result):
        logger.info(f"approved {len(result['handled'])}: {result['handled']}")
        if result["stopped"]:
            logger.error(f"stopped: {result['stopped']}")
        # The one thing this example must never print on a clean run.
        state = self._log_line()
        if state and "DESTROYED" in state:
            logger.error(f"page says: {state}")

    # -- one item -----------------------------------------------------------

    def _one_item(self, window) -> None:
        opener = window.find(identifier="next-item")
        if opener is None:
            raise PreconditionFailed("the queue page no longer has its button")
        opener.press()

        # Beat 1: wait for *a* dialog. Deliberately not for the one we want -
        # we have to look at what actually appeared before deciding.
        title = window.wait_for(role="AXHeading", timeout=10,
                                message="a dialog to open")
        self._check(window, title)

        approve = window.find(role="AXButton", text=self.EXPECTED_BUTTON)
        detail = window.find(identifier="confirm-detail")
        item = detail.all_text.strip() if detail else "?"

        # Beat 2: act.
        approve.press()
        self.handled.append(item)

        # Beat 3: wait for it to go before the next iteration starts.  Leaving
        # this out is what makes the next cycle press into a closing dialog.
        window.wait_until_gone(role="AXButton", text=self.EXPECTED_BUTTON,
                               timeout=10, message="the dialog to close")

    def _check(self, window, title) -> None:
        """Every precondition, before any press.

        Per step, not per action (§2.1): the screen can change between item 2
        and item 3, and usually does - that is what the fixture's second dialog
        is imitating.
        """
        # .name, not .all_text: a heading has its own label, and reaching for
        # all_text here is what surfaced two bugs at once - WebKit reports a
        # heading's level in AXValue, and its child restates the heading.
        heading = (title.name or title.all_text).strip()
        if heading != self.EXPECTED_TITLE:
            raise PreconditionFailed(
                f"dialog says {heading!r}, expected {self.EXPECTED_TITLE!r} - "
                f"the queue is done or the page changed; not pressing anything")
        button = window.find(role="AXButton", text=self.EXPECTED_BUTTON)
        if button is None:
            raise PreconditionFailed(
                f"no {self.EXPECTED_BUTTON!r} button in the dialog")

    def _log_line(self):
        window = self.ui.window(app=self.app_name)
        node = window.find(identifier="log") if window else None
        return node.all_text.strip() if node else None
