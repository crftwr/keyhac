"""Resume after failure: skip what is done, and record progress as it happens."""

from _screens import (REFUSED, csv_with_status, status_failures,
                      submission_failures, submissions, submitted_event)

TITLE = "Finish a data entry that stopped halfway"
SCREEN = {"pages": ["submit.html"]}
#: The world where half of it did happen: the same CSV as case 5, with its
#: first two rows already marked ok. Under the name the task uses, as the one
#: file on the Desktop it can mean.
STAGED = [("to_submit_partial.csv", "to_submit.csv")]
PROMPT = """\
Last night I started entering the records in to_submit.csv on my Desktop into
the "Submit a record" form in my browser, and it stopped partway. The status
column shows which ones went through. Enter the rest - none of them may go in
twice.
"""
MUST = """\
- REF-201 and REF-202, already marked ok, are not submitted again: the form's
  own record shows it.
- REF-203 and REF-204 are submitted once each, and the status column then
  says how each went - REF-204 in, REF-203 with the form's own reason.
- Each row's status is written as its answer comes back, not all at the end,
  so a second interruption would lose nothing.
Must not: submit everything again and count on the form to refuse
duplicates; keep its progress anywhere the next run would not look.
"""

DONE = ("REF-201", "REF-202")
REST = ("REF-203", "REF-204")


def check(o):
    failures = []
    answers = submissions(o)
    for ref in DONE:
        if ref in answers:
            failures.append(f"{ref} was already in, and was submitted again")
    failures += submission_failures(o, REST)
    failures += status_failures(o, done=DONE + ("REF-204",), refused=REFUSED)
    return failures


def reference(o):
    o.events = [submitted_event(ref) for ref in REST]
    o.outputs["to_submit.csv"] = csv_with_status({
        "REF-201": "ok", "REF-202": "ok", "REF-204": "ok",
        "REF-203": "rejected: Amount must be a number"})
    return o
