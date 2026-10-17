"""Form filling from a CSV: verified writes, and the form's own answer per row."""

import re

from _screens import (REFUSED, TO_SUBMIT, csv_with_status, status_failures,
                      submission_failures, submitted_event)

TITLE = "Enter a CSV into a web form and report the rejects"
SCREEN = {"pages": ["submit.html"]}
STAGED = [("to_submit.csv", "to_submit.csv")]
PROMPT = """\
Please enter every record in to_submit.csv on my Desktop into the "Submit a
record" form open in my browser. Then tell me which ones were rejected and
why, and put the result in the CSV's status column.
"""
MUST = """\
- REF-201, REF-202 and REF-204 are each accepted exactly once, with the
  title, amount and Urgent box the CSV gives them.
- REF-203 is refused; its status carries the form's own message ("Amount
  must be a number"), and the reply names it.
- Every field is confirmed to hold its value before Submit is pressed, and
  Submit is never pressed twice for one row.
Must not: write a field without reading it back; press the checkbox without
reading its state first.
"""

ACCEPTED = tuple(ref for ref in TO_SUBMIT if ref not in REFUSED)


def check(o):
    failures = submission_failures(o, tuple(TO_SUBMIT))
    failures += status_failures(o, done=ACCEPTED, refused=REFUSED)
    for ref in REFUSED:
        if not re.search(re.escape(ref), o.reply):
            failures.append(f"the reply does not name {ref} as rejected")
    return failures


def reference(o):
    o.events = [submitted_event(ref) for ref in TO_SUBMIT]
    o.outputs["to_submit.csv"] = csv_with_status(
        {ref: "accepted" for ref in ACCEPTED}
        | {ref: f"rejected: {reason}" for ref, reason in REFUSED.items()})
    o.reply = "Three went in. REF-203 was rejected: Amount must be a number."
    return o
