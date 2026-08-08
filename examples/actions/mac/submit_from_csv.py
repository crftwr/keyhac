"""Bulk form submission from a CSV, writing the outcome back into each row.

The §2 case that could not be written until there was a write side, and the
one with the failure mode that matters: a read that fails leaves nothing
behind, while a submission that fails halfway leaves a partial mutation in a
system that has already accepted it.

So the rules this demonstrates are the ones §2.1 asks for:

  - **Idempotent.** The status column is the checkpoint. A row already marked
    "ok" is skipped, so a rerun after a crash resumes rather than
    double-submitting - which no amount of undo could fix afterwards.
  - **Read the validation error back.** A form that rejects a row says why;
    without capturing that, "write the failure back to the row" is
    unimplementable and the operator gets "3 failed" with no idea which.
  - **Write the outcome per row, as it happens**, not at the end. A run killed
    mid-way must leave the CSV telling the truth about what got through.

Run it (macOS, Safari):

    python examples/actions/submit_from_csv.py

The fixture rejects the third row on purpose (its amount is not a number), so
a clean run ends with three accepted, one failed, and a CSV that says which.
"""

import csv
import pathlib
import shutil
import subprocess
import sys

_ACTIONS = pathlib.Path(__file__).resolve().parents[1]   # examples/actions
sys.path.insert(0, str(_ACTIONS.parents[1]))             # the repo root
sys.path.insert(0, str(_ACTIONS))                        # _runner.py, fixtures/

from _runner import front_window, run_action                      # noqa: E402
from keyhac.core.action import ThreadedAction                     # noqa: E402
from keyhac.core.fill import (FillFailed, press, set_checked,     # noqa: E402
                              set_text)
from keyhac.core.uitree import find_element                       # noqa: E402
from keyhac.core.wait import (evaluate_on_main_thread, wait_for,  # noqa: E402
                              wait_for_element)
from keyhac.core import log                                       # noqa: E402

logger = log.getLogger("SubmitFromCsv")

FIXTURES = _ACTIONS / "fixtures"
PAGE = (FIXTURES / "submit.html").as_uri()

#: CSV column -> the field on the form.  The mapping is the part a recorded
#: demonstration could never supply (§4): it lives in the operator's head.
FIELDS = {"ref": "ref", "title": "title", "amount": "amount"}


class SubmitFromCsv(ThreadedAction):
    """Submit every unprocessed row of a CSV through a form."""

    def __init__(self, source=None, app_name="Safari", url=PAGE, limit=100):
        self.source = pathlib.Path(source or (FIXTURES / "to_submit.csv"))
        self.app_name = app_name
        self.url = url
        self.limit = limit

    def starting(self):
        logger.info(f"submitting rows from {self.source.name}")

    def run(self):
        rows, fieldnames = self._read_rows()
        todo = [r for r in rows if r.get("status", "").strip() != "ok"]
        logger.info(f"{len(todo)} of {len(rows)} rows to submit")
        if not todo:
            return {"ok": 0, "failed": 0, "skipped": len(rows)}

        subprocess.run(["open", "-a", self.app_name, self.url], check=True)
        window = wait_for(lambda: front_window(self.app_name)[0], timeout=20,
                          message=f"{self.app_name} to open a window")
        wait_for_element(window, identifier="submit", timeout=20,
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
        for column, identifier in FIELDS.items():
            field = evaluate_on_main_thread(
                lambda i=identifier: find_element(window, identifier=i))
            if field is None:
                raise RuntimeError(f"no field {identifier!r} on the form")
            # set_text reads the value back; a write that did not take raises
            # rather than letting the row submit half-filled.
            set_text(field, row.get(column, ""))

        urgent = evaluate_on_main_thread(
            lambda: find_element(window, identifier="urgent"))
        if urgent is not None:
            # Read before toggling: pressing blindly would invert whatever the
            # previous row left behind.
            set_checked(urgent, row.get("urgent", "").strip().lower()
                        in ("yes", "true", "1"))

        button = evaluate_on_main_thread(
            lambda: find_element(window, identifier="submit"))
        press(button)

        message = self._result_message(window, row)
        if message.startswith("error:"):
            raise RuntimeError(message.removeprefix("error:").strip())

    def _result_message(self, window, row) -> str:
        """Whatever the form says happened - accepted, or why not."""
        def read():
            node = find_element(window, identifier="result")
            return node.all_text.strip() if node else ""

        reference = row.get("ref", "")
        try:
            return wait_for(
                lambda: read() or None, timeout=5,
                message=f"the form to respond to {reference}")
        except Exception:
            raise RuntimeError("the form never responded")

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
        # Replace atomically: the operator's record of what has been submitted
        # must never be a half-written file.
        shutil.move(str(temporary), str(self.source))


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run_action(SubmitFromCsv(source)))
