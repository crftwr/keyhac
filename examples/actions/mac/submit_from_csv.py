"""Bulk form submission from a CSV, writing the outcome back into each row.

macOS, Safari. The write side, and the failure mode that matters there: a read
that fails leaves nothing behind, while a submission that fails halfway leaves
a partial mutation in a system that has already accepted it. So:

  - **Idempotent.** The status column is the checkpoint. A row with an
    answer - "ok" or "refused" - is skipped, so a rerun after a crash resumes
    rather than double-submitting, which no undo could fix afterwards. A
    refused row goes in again only after the operator fixes it and clears its
    status; a row pressed with no answer is "unknown", left for them to check.
  - **Submit once, then wait for this row's answer.** The press has no
    `until=`, because a retried submit is a second record. The wait after it
    names *this* row's answer, because the result line is rewritten in place
    and the previous row's answer is still on it when the press returns.
  - **Read the validation error back** and write it into the row. Without it
    the operator gets "1 failed" and no idea which or why.
  - **Write the outcome per row, as it happens.** A run killed mid-way must
    leave the CSV telling the truth about what got through.

    python tools/run_action_file.py examples/actions/mac/submit_from_csv.py
        [source=~/Desktop/to_submit.csv]

The CSV is updated in place. When `source` does not exist yet it is seeded
from `fixtures/to_submit.csv`, so the example never rewrites the repository's
copy. The fixture refuses the third row on purpose (its amount is not a
number): a clean run ends with three accepted and one refused with the form's
own reason, and running again submits nothing.
"""

import csv
import os
import pathlib
import shutil
import subprocess

from keyhac import FillFailed, ThreadedAction, WaitTimeout, getLogger

logger = getLogger("SubmitFromCsv")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
PAGE = (FIXTURES / "submit.html").as_uri()

#: CSV column -> the form field's DOM id. The correspondence is the operator's
#: knowledge, not something either side could tell you.
FIELDS = {"ref": "ref", "title": "title", "amount": "amount"}

TRUE_WORDS = ("yes", "true", "1")


class SubmitFromCsv(ThreadedAction):
    """Submit every unprocessed row of a CSV through a form."""

    def __init__(self, source="~/Desktop/to_submit.csv", app_name="Safari",
                 url=PAGE, limit=100):
        self.source = pathlib.Path(source).expanduser()
        self.app_name = app_name
        self.url = url
        self.limit = limit

    def starting(self):
        logger.info(f"submitting rows from {self.source}")

    def run(self):
        ui = self.ui
        self.FIELD = {column: ui.Locator(identifier=dom_id)
                      for column, dom_id in FIELDS.items()}
        self.URGENT = ui.Locator(identifier="urgent")
        self.SUBMIT = ui.Locator(identifier="submit")
        self.RESULT = ui.Locator(identifier="result")

        if not self.source.exists():
            shutil.copyfile(FIXTURES / "to_submit.csv", self.source)
            logger.info(f"seeded {self.source} from the fixture")
        rows, fieldnames = self._read_rows()
        # Only rows Submit was never pressed for. A refused row is done - the
        # form answered it, and pressing again on the same values is a second
        # submission - and an unknown one may have gone in.
        todo = [r for r in rows
                if r.get("status", "").partition(":")[0].strip() in ("", "not sent")]
        logger.info(f"{len(todo)} of {len(rows)} rows to submit")
        outcome = {"ok": 0, "refused": 0, "unknown": 0, "not sent": 0,
                   "skipped": len(rows) - len(todo)}
        if not todo:
            return outcome

        subprocess.run(["open", "-a", self.app_name, self.url],
                       check=True, capture_output=True, text=True)
        window = ui.wait(ui.Appears(app=self.app_name, title="Submit a record"),
                         timeout=20, message="the form to open")
        ui.activate(node=window)
        self.front = ui.Front(node=window)
        window.wait_for(**self.SUBMIT.criteria(), timeout=20,
                        message="the form to load")

        for row in todo[:self.limit]:
            try:
                row["status"] = self._submit(window, row)
            except (FillFailed, RuntimeError) as error:
                # Raised before the press: nothing went in, so the next run
                # tries this row again.
                row["status"] = f"not sent: {error}"
            outcome[row["status"].partition(":")[0]] += 1
            if row["status"] != "ok":
                logger.error(f"{row.get('ref')}: {row['status']}")
            # After every row, not at the end: a run that dies here must leave
            # a file that still says what got through.
            self._write_rows(rows, fieldnames)
        return outcome

    def finished(self, result):
        logger.info(f"{result['ok']} submitted, {result['refused']} refused, "
                    f"{result['unknown']} unknown, {result['not sent']} not sent, "
                    f"{result['skipped']} already done -> {self.source}")
        # What to redo, and who does it: a refused row goes in again only once
        # the operator has fixed it, and an unknown one may already be in.
        if result["refused"]:
            logger.warning("refused rows: fix them in the CSV and clear their "
                           "status to submit them again")
        if result["unknown"]:
            logger.warning("unknown rows: check the form's records before "
                           "clearing their status - they may have gone in")
        if result["not sent"]:
            logger.warning("not-sent rows were never submitted; the next run "
                           "tries them again")

    # -- one row -------------------------------------------------------------

    def _submit(self, window, row) -> str:
        """Fill and submit one row, and return its status. Raises only before
        the press, when nothing has gone in."""
        ui = self.ui
        ref = row.get("ref", "").strip()
        for column, field in self.FIELD.items():
            # fill() focuses, writes, and reads the value back: a write that
            # did not take raises FillFailed instead of submitting half a row.
            ui.fill(row.get(column, ""), field, within=window, given=self.front)

        urgent = window.find(**self.URGENT.criteria())
        if urgent is None:
            raise RuntimeError("no #urgent checkbox on the form")
        # Read before toggling: pressing blindly would invert whatever the
        # previous row left behind.
        urgent.set_checked(row.get("urgent", "").strip().lower() in TRUE_WORDS)

        # Once, with no until=: a retry here is a second submission.
        ui.click(self.SUBMIT, within=window, given=self.front)
        try:
            answer = ui.wait(lambda: self._answer_for(window, ref), timeout=15,
                             message=f"the form to answer {ref or 'the blank row'}")
        except WaitTimeout:
            # Pressed, and no answer: it may have gone in. Not "failed", which
            # would invite pressing again.
            return "unknown: pressed Submit, no answer - check the form's records"
        if answer.startswith("error:"):
            # "error: REF-203: Amount must be a number" -> the form's reason.
            reason = answer.removeprefix("error:").strip()
            return f"refused: {reason.removeprefix(f'{ref}:').strip()}"
        return "ok"

    def _answer_for(self, window, ref: str) -> str | None:
        """The result line, once it is about this row - accepted or refused.

        Both outcomes count: waiting only for "accepted" would turn every
        refusal into a timeout, and the refusal's text is what goes into the
        row.
        """
        node = window.find(**self.RESULT.criteria())
        text = node.all_text.strip() if node else ""
        if not ref:
            return text if text == "error: Reference is required" else None
        if text == f"accepted {ref}" or text.startswith(f"error: {ref}:"):
            return text
        return None

    # -- the file ------------------------------------------------------------

    def _read_rows(self):
        with self.source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])
        if "status" not in fieldnames:
            fieldnames.append("status")
        return rows, fieldnames

    def _write_rows(self, rows, fieldnames) -> None:
        temporary = self.source.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        # Replaced atomically: the record of what has been submitted must
        # never be a half-written file.
        os.replace(temporary, self.source)
