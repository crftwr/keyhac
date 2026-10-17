"""Bulk form submission from a CSV, writing the outcome back into each row.

Windows, Microsoft Edge. The same task as `mac/submit_from_csv.py`, against
the same fixture form, and of the five pairs the one where the two files are
closest: every field has a DOM id, Chromium hands each one over as an
AutomationId, and an address by identifier is the one kind that crosses
platforms intact.

The rules are the macOS file's: the status column is the checkpoint, the
submit is pressed once and never retried, the wait after it names this row's
answer, the form's own refusal goes into the row, and the file is rewritten
after every row.

Two differences sit underneath the calls rather than in them. `ui.fill()`
pastes with Ctrl-V and reads the value back - which on Windows is not a
formality, since injected input is dropped often enough by some controls
that the read-back is what turns it into a loud `FillFailed`. And
`set_checked()` reads the checkbox's `ToggleState` where macOS reads a
value; the action does not need to know which.

    python tools/run_action_file.py examples/actions/win/submit_from_csv.py
        [source=~/Desktop/to_submit.csv]
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

#: CSV column -> the form field's DOM id.
FIELDS = {"ref": "ref", "title": "title", "amount": "amount"}

TRUE_WORDS = ("yes", "true", "1")


class SubmitFromCsv(ThreadedAction):
    """Submit every unprocessed row of a CSV through a form."""

    def __init__(self, source="~/Desktop/to_submit.csv", app_name="msedge",
                 url=PAGE, limit=100):
        self.source = pathlib.Path(source).expanduser()
        self.app_name = app_name
        self.url = url
        self.limit = limit

    def starting(self):
        logger.info(f"submitting rows from {self.source}")

    def run(self):
        ui = self.ui
        self.FIELD = {column: ui.Locator(role="Edit", identifier=dom_id)
                      for column, dom_id in FIELDS.items()}
        self.URGENT = ui.Locator(role="CheckBox", identifier="urgent")
        self.SUBMIT = ui.Locator(role="Button", identifier="submit")
        self.RESULT = ui.Locator(identifier="result")

        if not self.source.exists():
            shutil.copyfile(FIXTURES / "to_submit.csv", self.source)
            logger.info(f"seeded {self.source} from the fixture")
        rows, fieldnames = self._read_rows()
        todo = [r for r in rows if r.get("status", "").strip() != "ok"]
        logger.info(f"{len(todo)} of {len(rows)} rows to submit")
        if not todo:
            return {"ok": 0, "failed": 0, "skipped": len(rows)}

        subprocess.run(["cmd", "/c", "start", "", self.app_name, self.url],
                       check=True, capture_output=True, text=True)
        window = ui.wait(ui.Appears(app=self.app_name,
                                    title="Submit a record*"),
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
            ui.fill(row.get(column, ""), field, within=window, given=self.front)

        urgent = window.find(**self.URGENT.criteria())
        if urgent is None:
            raise RuntimeError("no #urgent checkbox on the form")
        urgent.set_checked(row.get("urgent", "").strip().lower() in TRUE_WORDS)

        # Once, with no until=: a retry here is a second submission.
        ui.click(self.SUBMIT, within=window, given=self.front)
        answer = ui.wait(lambda: self._answer_for(window, ref), timeout=15,
                         message=f"the form to answer {ref or 'the blank row'}")
        if answer.startswith("error:"):
            reason = answer.removeprefix("error:").strip()
            raise RuntimeError(reason.removeprefix(f"{ref}:").strip())

    def _answer_for(self, window, ref: str) -> str | None:
        """The result line, once it is about this row - accepted or refused."""
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
        # Replaced atomically - os.replace, not shutil.move, which on Windows
        # cannot rename over an existing file and falls back to a copy.
        os.replace(temporary, self.source)
