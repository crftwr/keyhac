"""Two systems, different column names: the mapping is data, per system."""

from _screens import SYSTEM_A, SYSTEM_B, records_failures

TITLE = "Merge two order systems into one CSV"
SCREEN = {"pages": ["systema_1.html", "systemb_1.html"]}
PROMPT = """\
I've got our two order systems open in the browser, SystemA and SystemB.
SystemB calls the same columns Reference, Title and Value. Merge every record
from both - all pages - into all_orders.csv on my Desktop, with one set of
headings (id, name, amount) and a column saying which system each record came
from.
"""
MUST = """\
- all_orders.csv has all 17 records once each - nine from SystemA, eight
  from SystemB - each labelled with the system it came from.
- SystemB's Reference/Title/Value land under id/name/amount by header name,
  with the correspondence written down as data, one entry per system.
- A table missing an expected column stops the run, naming the headers it
  did find - it is not mapped by position.
Must not: work out which column is which at run time; read only the first
page of either system.
"""

RECORDS = {**SYSTEM_A, **SYSTEM_B}
HEADINGS = ("id", "name", "amount")


def check(o):
    rows = o.csv_rows("all_orders.csv")
    if rows is None:
        return ["no all_orders.csv on the Desktop"]
    failures = []
    headers = list(rows[0]) if rows else []
    if missing := [h for h in HEADINGS if h not in headers]:
        failures.append(f"all_orders.csv's headings are {headers}, without {missing}")
    failures += records_failures(rows, RECORDS, "all_orders.csv")
    others = [h for h in headers if h not in HEADINGS]
    if not others:
        return failures + ["all_orders.csv has no column saying which system "
                           "a record came from"]
    for row in rows:
        record = row.get("id", "")
        want = "a" if record in SYSTEM_A else "b" if record in SYSTEM_B else None
        if want and not any(_system(row[h]) == want for h in others):
            failures.append(f"{record} is not labelled as System{want.upper()}")
            break
    return failures


def _system(cell):
    """'SystemA', 'system a', 'A' -> 'a'."""
    cell = cell.lower().replace(" ", "").replace("system", "")
    return cell if cell in ("a", "b") else None


def reference(o):
    lines = ["id,name,amount,system"]
    lines += [f"{i},{name},{amount},SystemA" for i, (name, amount) in SYSTEM_A.items()]
    lines += [f"{i},{name},{amount},SystemB" for i, (name, amount) in SYSTEM_B.items()]
    o.outputs["all_orders.csv"] = "\n".join(lines) + "\n"
    return o
