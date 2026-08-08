"""A deliberately wrong action, so the checker is tested in both directions.

Every problem below is one that was actually shipped at some point while the
examples were being written; this file is the regression fixture for
evals/check.py.  It is never executed.
"""

import time


class BadExtract:
    """Reads a paginated table. Wrongly."""

    def run(self, window, ui):
        rows = []
        # rule 1: waiting for time instead of for the page
        ui.press(window, "next")
        time.sleep(2)

        # rule 2: an element addressed by where it happened to be
        ui.click(640, 480)

        # rule 7: "Next" linking to itself runs until someone notices
        while True:
            for cell in ui.cells(window):
                rows.append(cell)
            # rule 6: raising after collecting discards everything above
            if not ui.find(window, "next"):
                raise RuntimeError("pagination ended unexpectedly")
            ui.press(window, "next")

        return rows

    def tick_the_box(self, box, ui):
        # rule 4: pressing a checkbox toggles it; this cannot be re-run
        box.perform_action("Toggle")

    def fill(self, field, ui):
        # rule 3: a write nobody checks
        ui.set_text(field, "REC-001", verify=False)
