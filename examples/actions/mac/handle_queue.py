"""Work a queue of confirmation dialogs - and refuse the one that is wrong.

macOS, Safari. Queue processing with per-item branching, the three beats of a
modal (it appears, you read and act, it is gone), and above all
**preconditions**: if the UI is not what this action was written against, it
stops and hands back to a human. It does not guess, because an action that
silently does the wrong thing is worse than one that refuses to run.

The fixture makes that concrete. After the real items are gone, the page shows
a *different* dialog - "Delete all records?" - with the same shape and a first
button that destroys everything. A handler that presses "the first button in
the dialog", or trusts the dialog it saw last time to be the one on screen now,
deletes the records and reports success. This one reads what appeared before
every press, and stops.

    python tools/run_action_file.py examples/actions/mac/handle_queue.py
"""

import pathlib
import subprocess

from keyhac import StaleElement, ThreadedAction, WaitTimeout, getLogger

logger = getLogger("HandleQueue")

FIXTURE = (pathlib.Path(__file__).resolve().parents[1]
           / "fixtures" / "dialog.html").as_uri()


class PreconditionFailed(RuntimeError):
    """The screen is not what this action was written against.

    Its own type so the caller can tell "the UI moved on me" from "the thing I
    was doing failed" - the first means regenerate the action, the second
    means retry it.
    """


def is_dialog(node) -> bool:
    """An HTML <dialog> opened modally: WebKit reports an AXGroup with the
    AXApplicationDialog subrole, and hides the rest of the page while it is
    up. The subrole is outside UINode's projection, so it is read from the
    platform element."""
    return node.element.get_attribute_value("AXSubrole") == "AXApplicationDialog"


class HandleQueue(ThreadedAction):
    """Approve every item in the queue, one dialog at a time."""

    #: What the one dialog this action may touch says. Read before anything
    #: is pressed; anything else on screen means stop.
    EXPECTED_TITLE = "Approve this item?"

    def __init__(self, app_name="Safari", url=FIXTURE, limit=10):
        self.app_name = app_name
        self.url = url
        self.limit = limit
        self.handled: list[str] = []
        self.stopped_because: str | None = None

    def starting(self):
        logger.info("working the approval queue")

    def run(self):
        ui = self.ui
        self.OPEN_NEXT = ui.Locator(identifier="next-item")
        self.DIALOG = ui.Locator(role="Group", predicate=is_dialog)
        self.HEADING = ui.Locator(role="Heading", max_depth=1)
        self.DETAIL = ui.Locator(identifier="confirm-detail", max_depth=2)
        self.APPROVE = ui.Locator(role="Button", name="Approve", max_depth=1)

        subprocess.run(["open", "-a", self.app_name, self.url],
                       check=True, capture_output=True, text=True)
        window = ui.wait(ui.Appears(app=self.app_name, title="Queue*"),
                         timeout=20, message="the queue page to open")
        ui.activate(node=window)
        self.front = ui.Front(node=window)

        for _ in range(self.limit):
            try:
                self._one_item(window)
            except PreconditionFailed as error:
                # Not an error in the run - it is the run correctly declining
                # to continue. Everything already done stays done.
                self.stopped_because = str(error)
                break
            except WaitTimeout as error:
                self.stopped_because = f"gave up waiting: {error}"
                break
            except StaleElement as error:
                # The page moved under a retry - a dialog took the front, the
                # button left the tree - and nothing more was pressed into it.
                self.stopped_because = f"the page changed: {error}"
                break
        return {"handled": self.handled, "stopped": self.stopped_because,
                "page_says": self._log_line(window)}

    def finished(self, result):
        logger.info(f"approved {len(result['handled'])}: {result['handled']}")
        if result["stopped"]:
            logger.error(f"stopped: {result['stopped']}")
        # The one thing this example must never print.
        if "DESTROYED" in (result["page_says"] or ""):
            logger.error(f"page says: {result['page_says']}")

    # -- one item ------------------------------------------------------------

    def _one_item(self, window) -> None:
        ui = self.ui
        # Beat 1: open the next item, and wait for *a* dialog - deliberately
        # not for the one we want. What appeared has to be read before
        # anything is decided, and that reading is beat 2's first job. A
        # dialog already up - a run stopped mid-item - is read the same way
        # instead of being opened again: the page behind it is inert.
        dialog = (window.find(**self.DIALOG.criteria())
                  or ui.click(self.OPEN_NEXT, within=window, given=self.front,
                              until=ui.Appears(self.DIALOG, within=window)))

        heading = dialog.find(**self.HEADING.criteria())
        # .name, not .all_text: a heading has its own label, and WebKit puts
        # the heading level in its AXValue.
        title = heading.name.strip() if heading and heading.name else None
        if title != self.EXPECTED_TITLE:
            raise PreconditionFailed(
                f"a dialog says {title!r}, expected {self.EXPECTED_TITLE!r} - "
                f"the queue is done or the page changed; pressed nothing")
        detail = dialog.find(**self.DETAIL.criteria())
        item = detail.all_text.strip() if detail else "?"

        # Beat 2 acts, and beat 3 is its postcondition: the dialog going away
        # is what an accepted Approve looks like. `given=` re-reads the heading
        # before every attempt, so a retry can never land on a dialog that
        # replaced this one in the meantime.
        ui.click(self.APPROVE, within=dialog,
                 given=ui.Reads(heading, name=self.EXPECTED_TITLE),
                 until=ui.Gone(self.DIALOG, within=window))
        self.handled.append(item)

    def _log_line(self, window) -> str | None:
        node = window.find(identifier="log")
        return node.all_text.strip() if node else None
