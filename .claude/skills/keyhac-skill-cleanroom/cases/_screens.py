"""What the fixture screens hold, for the cases that are checked against them.

Written out from examples/actions/fixtures/ by hand rather than parsed out of
the HTML: a check that read its expectations from the page it is checking
would agree with any page, including a broken one.
"""

#: SystemA's three pages: REC-001 .. REC-009.
SYSTEM_A = {f"REC-{n:03d}": (f"item {n}", str(37 * n)) for n in range(1, 10)}

#: SystemB's two pages: REF-100 .. REF-107.
SYSTEM_B = {f"REF-{100 + n}": (f"item {100 + n}", str(3700 + 37 * n))
            for n in range(8)}

#: to_submit.csv: ref -> (title, amount, urgent). REF-203's amount is what the
#: form refuses.
TO_SUBMIT = {
    "REF-201": ("first item", "120", True),
    "REF-202": ("second item", "340", False),
    "REF-203": ("bad amount", "not-a-number", False),
    "REF-204": ("fourth item", "75", True),
}
REFUSED = {"REF-203": "Amount must be a number"}


def records_failures(rows, expected, name):
    """Every expected record in ``rows`` once, with its amount."""
    failures = []
    seen = {}
    for row in rows:
        ids = [cell for cell in row.values() if cell in expected]
        if ids:
            seen.setdefault(ids[0], []).append(row)
    if missing := sorted(set(expected) - set(seen)):
        failures.append(f"{name} is missing {len(missing)} record(s): "
                        f"{', '.join(missing[:5])}")
    if doubled := sorted(i for i, found in seen.items() if len(found) > 1):
        failures.append(f"{name} has these more than once: {', '.join(doubled)}")
    if extra := len(rows) - sum(len(found) for found in seen.values()):
        failures.append(f"{name} has {extra} row(s) that are no record on screen")
    for record, found in seen.items():
        amount = expected[record][1]
        if not any(amount in row.values() for row in found):
            failures.append(f"{record}'s amount is not {amount}")
    return failures


def submissions(o):
    """What the form recorded: ref -> [its answers, in order]."""
    answers = {}
    for event in o.screen("submit"):
        answers.setdefault(event.get("ref", ""), []).append(event)
    return answers


def submission_failures(o, refs):
    """``refs`` each accepted exactly once with the CSV's own values, and the
    refused ones tried; nothing else accepted."""
    failures = []
    answers = submissions(o)
    for ref in refs:
        title, amount, urgent = TO_SUBMIT[ref]
        tried = answers.get(ref, [])
        accepted = [e for e in tried if str(e.get("answer", "")).startswith("accepted")]
        if ref in REFUSED:
            if not tried:
                failures.append(f"{ref} was never submitted, so the form never "
                                f"said why it refuses it")
            continue
        if len(accepted) != 1:
            failures.append(f"{ref} was accepted {len(accepted)} times, not once")
            continue
        got = accepted[0]
        if (got.get("title"), got.get("amount"), bool(got.get("urgent"))) != (title, amount, urgent):
            failures.append(f"{ref} went in as title={got.get('title')!r} "
                            f"amount={got.get('amount')!r} urgent={got.get('urgent')}, "
                            f"not {title!r}, {amount!r}, {urgent}")
    for ref, tried in answers.items():
        if ref not in refs and any(str(e.get("answer", "")).startswith("accepted")
                                   for e in tried):
            failures.append(f"{ref or 'an empty form'} was submitted, which it should not have been")
    return failures


def submitted_event(ref, answer=None):
    """The record the form makes of one submission, for reference outcomes."""
    title, amount, urgent = TO_SUBMIT[ref]
    if answer is None:
        answer = (f"error: {ref}: {REFUSED[ref]}" if ref in REFUSED
                  else f"accepted {ref}")
    return {"page": "submit.html", "what": "submit", "ref": ref, "title": title,
            "amount": amount, "urgent": urgent, "answer": answer}


def status_failures(o, done, refused):
    """The CSV's status column: ``done`` refs read as accepted, ``refused``
    refs carry the form's own reason."""
    rows = o.csv_rows("to_submit.csv")
    if rows is None:
        return ["to_submit.csv is gone from the Desktop"]
    by_ref = {row.get("ref", ""): row for row in rows}
    failures = []
    for ref in done:
        status = by_ref.get(ref, {}).get("status", "")
        if not status or "error" in status.lower() or "reject" in status.lower():
            failures.append(f"to_submit.csv's status for {ref} is {status!r}, "
                            f"not that it went in")
    for ref in refused:
        status = by_ref.get(ref, {}).get("status", "")
        if REFUSED[ref].lower() not in status.lower():
            failures.append(f"to_submit.csv's status for {ref} is {status!r}: it "
                            f"does not carry the form's reason, "
                            f"{REFUSED[ref]!r}")
    return failures


def csv_with_status(statuses):
    """to_submit.csv with the given status per ref."""
    lines = ["ref,title,amount,urgent,status"]
    for ref, (title, amount, urgent) in TO_SUBMIT.items():
        lines.append(f"{ref},{title},{amount},{'yes' if urgent else 'no'},"
                     f"{statuses.get(ref, '')}")
    return "\n".join(lines) + "\n"
