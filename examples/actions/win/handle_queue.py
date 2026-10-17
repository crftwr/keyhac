"""Work a queue of confirmation dialogs - and refuse the one that is wrong.

Windows, Microsoft Edge. The same task as `mac/handle_queue.py`, against the
same fixture: approve each item, reading every dialog before pressing
anything, and stop at the "Delete all records?" dialog that has the same shape
and a first button that destroys everything.

The three beats and the preconditions are the macOS file's unchanged. What
changed is how a dialog is recognised. WebKit marks an open <dialog> with a
subrole; Chromium reports it under UI Automation as a `Pane`, a control type
the page has no other use for - but so does the browser's own chrome, so the
search is scoped to the page's `Document` and confirmed by the heading inside
it, and never run over the whole window.

    python tools/run_action_file.py examples/actions/win/handle_queue.py
"""

import pathlib
import subprocess

from keyhac import ThreadedAction, WaitTimeout, getLogger

logger = getLogger("HandleQueue")

FIXTURE = (pathlib.Path(__file__).resolve().parents[1]
           / "fixtures" / "dialog.html").as_uri()


class PreconditionFailed(RuntimeError):
    """The screen is not what this action was written against - regenerate,
    rather than retry."""


def is_dialog(node) -> bool:
    """A modal <dialog>: a Pane in the page with a heading of its own."""
    return node.find(role="Heading", max_depth=2) is not None


class HandleQueue(ThreadedAction):
    """Approve every item in the queue, one dialog at a time."""

    EXPECTED_TITLE = "Approve this item?"

    def __init__(self, app_name="msedge", url=FIXTURE, limit=10):
        self.app_name = app_name
        self.url = url
        self.limit = limit
        self.handled: list[str] = []
        self.stopped_because: str | None = None

    def starting(self):
        logger.info("working the approval queue")

    def run(self):
        ui = self.ui
        self.PAGE = ui.Locator(role="Document", name="Queue*")
        self.OPEN_NEXT = ui.Locator(identifier="next-item")
        self.DIALOG = ui.Locator(role="Pane", predicate=is_dialog)
        self.HEADING = ui.Locator(role="Heading", max_depth=2)
        self.DETAIL = ui.Locator(identifier="confirm-detail", max_depth=3)
        self.APPROVE = ui.Locator(role="Button", name="Approve", max_depth=2)

        subprocess.run(["cmd", "/c", "start", "", self.app_name, self.url],
                       check=True, capture_output=True, text=True)
        window = ui.wait(ui.Appears(app=self.app_name, title="Queue*"),
                         timeout=20, message="the queue page to open")
        ui.activate(node=window)
        self.front = ui.Front(node=window)
        page = window.wait_for(**self.PAGE.criteria(), timeout=20,
                               message="the queue page's document")

        for _ in range(self.limit):
            try:
                self._one_item(page)
            except PreconditionFailed as error:
                self.stopped_because = str(error)
                break
            except WaitTimeout as error:
                self.stopped_because = f"gave up waiting: {error}"
                break
        return {"handled": self.handled, "stopped": self.stopped_because,
                "page_says": self._log_line(page)}

    def finished(self, result):
        logger.info(f"approved {len(result['handled'])}: {result['handled']}")
        if result["stopped"]:
            logger.error(f"stopped: {result['stopped']}")
        if "DESTROYED" in (result["page_says"] or ""):
            logger.error(f"page says: {result['page_says']}")

    # -- one item ------------------------------------------------------------

    def _one_item(self, page) -> None:
        ui = self.ui
        # Beat 1: wait for *a* dialog, not the one we want - or take the one
        # already up, when a run stopped mid-item.
        dialog = (page.find(**self.DIALOG.criteria())
                  or ui.click(self.OPEN_NEXT, within=page, given=self.front,
                              until=ui.Appears(self.DIALOG, within=page)))

        heading = dialog.find(**self.HEADING.criteria())
        title = heading.name.strip() if heading and heading.name else None
        if title != self.EXPECTED_TITLE:
            raise PreconditionFailed(
                f"a dialog says {title!r}, expected {self.EXPECTED_TITLE!r} - "
                f"the queue is done or the page changed; pressed nothing")
        detail = dialog.find(**self.DETAIL.criteria())
        item = detail.all_text.strip() if detail else "?"

        # Beats 2 and 3: act, with the dialog going away as the postcondition,
        # and the heading re-read before every attempt.
        ui.click(self.APPROVE, within=dialog,
                 given=ui.Reads(heading, name=self.EXPECTED_TITLE),
                 until=ui.Gone(self.DIALOG, within=page))
        self.handled.append(item)

    def _log_line(self, page) -> str | None:
        node = page.find(identifier="log")
        return node.all_text.strip() if node else None
