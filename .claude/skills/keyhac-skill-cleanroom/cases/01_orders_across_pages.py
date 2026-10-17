"""Pagination: follow Next to the end, and know when you are there."""

from _screens import SYSTEM_A, records_failures

TITLE = "Save a paged order list to CSV"
#: The runner serves this page and opens it before the room starts. The task
#: names the system the way the screen does - an internal tool's own name.
SCREEN = {"pages": ["systema_1.html"]}
PROMPT = """\
The order list in my browser (SystemA) runs over several pages. I need all of
it in a spreadsheet: save every order to orders.csv on my Desktop, with its
ID, name and amount.
"""
MUST = """\
- orders.csv holds every order on every page - nine, REC-001 to REC-009 -
  once each, with the amount the page shows.
- It moves on when the page says it has (the title, or "page N of 3"), not
  after a fixed delay, and stops at the last page without being told how many
  there are.
- The page loop is bounded: a Next that led back to itself could not run
  forever.
- The file is written once at the end, or keyed by order ID, so running it
  again does not double it.
Must not: find "page 1 of 3" by a DOM id (it is a plain span and has none);
take "the tree stopped changing" as having reached the next page.
"""


def check(o):
    rows = o.csv_rows("orders.csv")
    if rows is None:
        return ["no orders.csv on the Desktop"]
    return records_failures(rows, SYSTEM_A, "orders.csv")


def reference(o):
    lines = ["id,name,amount"] + [f"{i},{name},{amount}"
                                  for i, (name, amount) in SYSTEM_A.items()]
    o.outputs["orders.csv"] = "\n".join(lines) + "\n"
    return o
