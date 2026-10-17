"""Bulk form submission from a CSV, writing the outcome back into each row.

macOS, Safari. The write side, and the failure mode that matters there: a read
that fails leaves nothing behind, while a submission that fails halfway leaves
a partial mutation in a system that has already accepted it. So:

  - **Idempotent.** The status column is the checkpoint. A row already marked
    "ok" is skipped, so a rerun after a crash resumes rather than
    double-submitting - which no undo could fix afterwards.
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
number): a clean run ends with three accepted and one failed with the form's
own reason, and running again submits nothing but that one.
"""

import csv
import os
import pathlib
import shutil
import subprocess

from keyhac import FillFailed, ThreadedAction, getLogger

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
        todo = [r for r in rows if r.get("status", "").strip() != "ok"]
        logger.info(f"{len(todo)} of {len(rows)} rows to submit")
        if not todo:
            return {"ok": 0, "failed": 0, "skipped": len(rows)}

        subprocess.run(["open", "-a", self.app_name, self.url],
                       check=True, capture_output=True, text=True)
        window = ui.wait(ui.Appears(app=self.app_name, title="Submit a record"),
                         timeout=20, message="the form to open")
        ui.activate(node=window)
        self.front = ui.Front(node=window)
        window.wait_for(**self.SUBMIT.criteria(), timeout=20,
                        message="the form to load")

        ok = failed = 0
        for row in todo[:self.limit]:
            try:
                self._submit(window, row)
                row["status"] = "ok"
                ok += 1
            except (FillFailed, RuntimeError) as error:
                row["status"] = f"failed: {error}"
                failed += 1
                logger.error(f"{row.get('ref')}: {error}")
            # After every row, not at the end: a run that dies here must leave
            # a file that still says what got through.
            self._write_rows(rows, fieldnames)
        return {"ok": ok, "failed": failed, "skipped": len(rows) - len(todo)}

    def finished(self, result):
        logger.info(f"{result['ok']} submitted, {result['failed']} failed, "
                    f"{result['skipped']} already done -> {self.source}")

    # -- one row -------------------------------------------------------------

    def _submit(self, window, row) -> None:
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
        answer = ui.wait(lambda: self._answer_for(window, ref), timeout=15,
                         message=f"the form to answer {ref or 'the blank row'}")
        if answer.startswith("error:"):
            # "error: REF-203: Amount must be a number" -> the form's reason.
            reason = answer.removeprefix("error:").strip()
            raise RuntimeError(reason.removeprefix(f"{ref}:").strip())

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
