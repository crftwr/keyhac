"""Cross-system extraction: read two systems that disagree, write one CSV.

The first of the hand-written actions in this directory,
and the shape §2 says the interesting work actually has:

    enumerate targets -> for each: navigate, wait, read, accumulate
                      -> normalise -> emit in an external format

Nothing here infers anything. Locating a table is a tree search, following
pagination is a loop, normalising column names is a dict, and writing CSV is
the standard library. That the whole thing comes out as plain Python is the
point being demonstrated, not a coincidence.

Run it (macOS, Safari, no configuration needed):

    python tools/run_action_file.py examples/actions/mac/extract_records.py

WHAT IT IS BUILT TO SURVIVE
  - A page that has not arrived yet.  Every navigation is followed by a wait
    on something specific - the page label changing - never a sleep.
  - A page that fails.  One bad page does not lose the pages already read; it
    is recorded and the run continues, and the summary says what to redo.
  - Being run twice.  Rows are keyed, so re-running after a partial failure
    merges rather than duplicating.
"""

import csv
import pathlib
import subprocess

from keyhac import ThreadedAction, WaitTimeout, getLogger

logger = getLogger("ExtractRecords")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

#: The systems to read, and what each calls the three columns we want.  This
#: mapping is the whole of "normalisation" - the part a demonstration could
#: never show, because it happens in the operator's head (§4).
SYSTEMS = [
    {"name": "SystemA",
     "url": (FIXTURES / "systema_1.html").as_uri(),
     "columns": {"ID": "id", "Name": "name", "Amount": "amount"}},
    {"name": "SystemB",
     "url": (FIXTURES / "systemb_1.html").as_uri(),
     "columns": {"Reference": "id", "Title": "name", "Value": "amount"}},
]

FIELDS = ["system", "id", "name", "amount"]


class ExtractRecords(ThreadedAction):
    """Walk every page of every system and write one normalised CSV."""

    def __init__(self, systems=SYSTEMS, output_path="~/Desktop/records.csv",
                 app_name="Safari", page_limit=50):
        self.systems = systems
        self.output_path = pathlib.Path(output_path).expanduser()
        self.app_name = app_name
        #: A runaway "Next" - a page linking to itself - would otherwise loop
        #: for as long as the operator's patience.
        self.page_limit = page_limit
        #: The progress log.  Recorded per page, because that is the unit a
        #: resumed run would restart from (§2.1: checkpoint before rollback).
        self.done: list[str] = []
        self.failed: list[tuple[str, str]] = []

    # -- lifecycle ----------------------------------------------------------

    def starting(self):
        logger.info(f"extracting {len(self.systems)} systems "
                    f"-> {self.output_path}")

    def run(self):
        # Accumulated here rather than returned, so a system that throws
        # halfway keeps the pages it already read.  Written as a local first,
        # which silently discarded them - the failure mode this whole class of
        # action exists to avoid, and it survived one review of its own
        # docstring claiming otherwise.
        rows: list[dict] = []
        for system in self.systems:
            try:
                self._read_system(system, rows)
            except Exception as error:
                # A system that fails must not take the ones already read with
                # it: this is an ETL run, not a transaction.
                self.failed.append((system["name"], str(error)))
                logger.error(f"{system['name']}: {error}")
        written = self._write(rows)
        return {"rows": len(rows), "written": written,
                "pages": self.done, "failed": self.failed}

    def finished(self, result):
        if result["failed"]:
            logger.error(
                f"{result['rows']} rows written to {self.output_path}, but "
                f"{len(result['failed'])} system(s) failed - rerun: "
                + ", ".join(name for name, _ in result["failed"]))
        else:
            logger.info(f"{result['rows']} rows from {len(result['pages'])} "
                        f"pages -> {self.output_path}")

    # -- the pipeline -------------------------------------------------------

    def _read_system(self, system, rows: list[dict]) -> None:
        ui = self.ui
        self._open(system["url"])
        window = ui.wait(lambda: ui.window(app=self.app_name), timeout=20,
                         message=f"{self.app_name} to open a window")
        # Precondition per step, not only per action: the right page really is
        # the one on screen before anything is read off it.
        window.wait_for(role="AXTable", timeout=20,
                        message=f"{system['name']} to load a result table")

        page = 0
        while page < self.page_limit:
            page += 1
            label = self._page_label(window)
            rows += self._read_page(window, system)
            self.done.append(f"{system['name']} {label or f'page {page}'}")

            following = (window.find(identifier="next")
                         or window.find(role="AXLink", text="Next"))
            if following is None:
                break
            following.press()
            # Wait for the page to actually change, not for time to pass: the
            # label is the one thing guaranteed to differ between pages.
            self._wait_for_new_page(window, label)
        else:
            raise RuntimeError(f"more than {self.page_limit} pages; "
                               f"is 'Next' linking to itself?")

    def _read_page(self, window, system) -> list[dict]:
        """One table, normalised. Returns [] rather than raising on an empty
        page - a search with no results is an answer, not a failure."""
        table = window.find(role="AXTable")
        grid = table and [[cell.all_text.strip()
                           for cell in row.children if cell.role == "AXCell"]
                          for row in table.children if row.role == "AXRow"]
        if not grid:
            return []

        header, *body = grid
        # Map this system's column names onto ours, and ignore columns we were
        # not asked for rather than failing on them.
        wanted = {index: system["columns"][name]
                  for index, name in enumerate(header)
                  if name in system["columns"]}
        missing = set(system["columns"]) - set(header)
        if missing:
            raise RuntimeError(
                f"{system['name']} is missing column(s) {sorted(missing)}; "
                f"found {header}. The page changed - regenerate this action.")

        rows = []
        for cells in body:
            row = {"system": system["name"]}
            for index, field in wanted.items():
                row[field] = cells[index] if index < len(cells) else ""
            rows.append(row)
        return rows

    def _page_label(self, window) -> str | None:
        """Something that differs between pages, to wait on after Next.

        The obvious `<span id="page">page 1 of 3</span>` is unusable: WebKit
        gives an AXDOMIdentifier to controls, tables and landmarks, but a plain
        span collapses into bare AXStaticText with no identifier at all - so
        find_element(identifier="page") returns None on every page and the
        wait can never see a change.  The document title survives into the web
        area's name, which is both stable and free.
        """
        web_area = window.find(role="AXWebArea")
        if web_area is not None and web_area.name:
            return web_area.name
        # Fall back to the on-page text, matched by what it says rather than by
        # an id it does not have.
        node = window.find(role="AXStaticText", value="page*of*")
        return node.all_text.strip() if node else None

    def _wait_for_new_page(self, window, previous_label: str | None) -> None:
        try:
            self.ui.wait(
                lambda: self._page_label(window) != previous_label, timeout=20,
                message=f"the page after {previous_label!r} to load")
        except WaitTimeout:
            raise RuntimeError(
                f"stuck on {previous_label!r} after pressing Next")

    # -- edges --------------------------------------------------------------

    def _open(self, url: str) -> None:
        subprocess.run(["open", "-a", self.app_name, url], check=True)

    def _write(self, rows: list[dict]) -> int:
        """Merge into the CSV, keyed by (system, id).

        Idempotent on purpose: a run that failed halfway is rerun, and the
        cheapest way to make that safe is to key the rows rather than append
        them (§2.1).
        """
        existing = {}
        if self.output_path.exists():
            with self.output_path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    existing[(row.get("system"), row.get("id"))] = row
        for row in rows:
            existing[(row["system"], row["id"])] = row

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for key in sorted(existing, key=lambda k: (k[0] or "", k[1] or "")):
                writer.writerow({field: existing[key].get(field, "")
                                 for field in FIELDS})
        return len(existing)
