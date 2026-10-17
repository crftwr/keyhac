"""Work a queue of confirmation dialogs - and refuse the one that is wrong.

Windows, Microsoft Edge. The same task as `mac/handle_queue.py`, against the
same fixture: approve each item, reading every dialog before pressing
anything, and stop at the "Delete all records?" dialog that has the same shape
and a first button that destroys everything.

The three beats and the preconditions are the macOS file's unchanged. What
changed is how the dialog and its words are found - measured on Edge, and
wrong in all three places on the first guess:

  - **An open <dialog> is a `Window`.** WebKit marks it with a subrole;
    Chromium gives it the control type a top-level window has. So does the
    browser around the page, which is why the search is scoped to the page's
    `Document` and never run over the whole browser window.
  - **A heading is a `Text`.** UI Automation has no heading control type, so
    the dialog's <h2> arrives as plain text - its first one.
  - **A <p> has no AutomationId.** Chromium passes DOM ids through for
    controls and headings, not for a paragraph, so the item's detail is read
    by position - the dialog's second text - rather than by its id.

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
        self.DIALOG = ui.Locator(role="Window")      # within the Document only
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

        # The dialog's own texts, in order: its heading, then what it is about.
        texts = [child for child in dialog.reread(max_depth=2).children
                 if child.role == "Text"]
        heading = texts[0] if texts else None
        title = heading.name.strip() if heading and heading.name else None
        if title != self.EXPECTED_TITLE:
            raise PreconditionFailed(
                f"a dialog says {title!r}, expected {self.EXPECTED_TITLE!r} - "
                f"the queue is done or the page changed; pressed nothing")
        item = texts[1].all_text.strip() if len(texts) > 1 else "?"

        # Beats 2 and 3: act, with the dialog going away as the postcondition,
        # and the heading re-read before every attempt.
        ui.click(self.APPROVE, within=dialog,
                 given=ui.Reads(heading, name=self.EXPECTED_TITLE),
                 until=ui.Gone(self.DIALOG, within=page))
        self.handled.append(item)

    def _log_line(self, page) -> str | None:
        node = page.find(identifier="log")
        return node.all_text.strip() if node else None
