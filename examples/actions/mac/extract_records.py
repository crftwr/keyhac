"""Cross-system extraction: read two systems that disagree, write one CSV.

macOS, Safari. The shape most real extraction work has:

    enumerate targets -> for each: navigate, wait, read, accumulate
                      -> normalise -> emit in an external format

Nothing here infers anything. Locating a table is a tree search, following
pagination is a loop, normalising column names is a dict, and writing CSV is
the standard library.

    python tools/run_action_file.py examples/actions/mac/extract_records.py
        [output_path=~/Desktop/records.csv]

WHAT IT IS BUILT TO SURVIVE
  - A page that has not arrived yet. Next is a `ui.click` whose `until=` names
    the page that must come up ("page 2 of 3"), so the click is retried if
    Safari accepted the press and did nothing, and the read never starts on
    the page being left.
  - A page that fails. One bad system does not lose the pages already read;
    it is recorded and the run continues, and the summary says what to redo.
  - Being run twice. Rows are keyed, so re-running after a partial failure
    merges rather than duplicating.
"""

import csv
import pathlib
import re
import subprocess

from keyhac import ThreadedAction, getLogger

logger = getLogger("ExtractRecords")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

#: The systems to read, and what each calls the three columns we want. This
#: mapping is the whole of "normalisation", and it is domain knowledge: it
#: lives in the operator's head, not on either screen.
SYSTEMS = [
    {"name": "SystemA",
     "url": (FIXTURES / "systema_1.html").as_uri(),
     "columns": {"ID": "id", "Name": "name", "Amount": "amount"}},
    {"name": "SystemB",
     "url": (FIXTURES / "systemb_1.html").as_uri(),
     "columns": {"Reference": "id", "Title": "name", "Value": "amount"}},
]

FIELDS = ["system", "id", "name", "amount"]

#: "page 2 of 3" - read off the page, never counted by the action.
PAGE_LABEL = re.compile(r"page (\d+) of (\d+)")


class ChangedScreen(RuntimeError):
    """The page is not the one this action was written against - regenerate
    it. Kept apart from a step that merely failed, which a rerun fixes."""


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
        self.done: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def starting(self):
        logger.info(f"extracting {len(self.systems)} systems "
                    f"-> {self.output_path}")

    def run(self):
        ui = self.ui
        # Selectors are values, written once. The table has a DOM id; the page
        # label is a plain <span>, which WebKit collapses into an anonymous
        # StaticText with no id at all - so it is addressed by what it says.
        self.TABLE = ui.Locator(identifier="results", max_depth=8)
        self.ROW = ui.Locator(role="Row", max_depth=3)
        self.NEXT = ui.Locator(role="Link", name="Next", max_depth=8)

        # Accumulated here rather than returned from the per-system call, so a
        # system that throws halfway keeps the pages it already read.
        rows: list[dict] = []
        for system in self.systems:
            try:
                self._read_system(system, rows)
            except Exception as error:
                # An ETL run, not a transaction: one system failing must not
                # take the ones already read with it.
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

    # -- one system ----------------------------------------------------------

    def _read_system(self, system, rows: list[dict]) -> None:
        ui = self.ui
        subprocess.run(["open", "-a", self.app_name, system["url"]],
                       check=True, capture_output=True, text=True)
        # The window is chosen by what it shows - the system's own title - and
        # then pinned by identity, because that title changes on every page.
        window = ui.wait(ui.Appears(app=self.app_name,
                                    title=f"{system['name']} *"),
                         timeout=20, message=f"{system['name']} to open")
        ui.activate(node=window)
        front = ui.Front(node=window)
        window.wait_for(**self.TABLE.criteria(), timeout=20,
                        message=f"{system['name']} to show its result table")

        for _ in range(self.page_limit):
            page, last = self._page_number(window)
            rows += self._read_page(window, system)
            self.done.append(f"{system['name']} page {page} of {last}")
            if page == last:
                return
            # The postcondition names the page that must come up, not "the
            # label changed": a state you can say is a state you can check.
            ui.click(self.NEXT, within=window, given=front,
                     until=ui.Appears(
                         ui.Locator(role="StaticText",
                                    text=f"page {page + 1} of {last}*"),
                         within=window),
                     timeout=20)
        raise RuntimeError(f"more than {self.page_limit} pages; "
                           f"is 'Next' linking to itself?")

    def _page_number(self, window) -> tuple[int, int]:
        label = window.find(role="StaticText", text="page * of *")
        match = PAGE_LABEL.search(label.text if label else "")
        if match is None:
            raise ChangedScreen("no 'page N of M' label on the page")
        return int(match.group(1)), int(match.group(2))

    def _read_page(self, window, system) -> list[dict]:
        """One table, normalised. [] for an empty table - a search with no
        results is an answer, not a failure."""
        table = window.find(**self.TABLE.criteria())
        if table is None:
            raise ChangedScreen(f"{system['name']} has no #results table")
        # Each row comes back carrying its cells; a cell keeps its string in a
        # child StaticText, which is what all_text is for.
        grid = [[cell.all_text.strip()
                 for cell in row.children if cell.role == "AXCell"]
                for row in table.find_all(**self.ROW.criteria())]
        if not grid:
            return []

        header, *body = grid
        missing = set(system["columns"]) - set(header)
        if missing:
            raise ChangedScreen(
                f"{system['name']} is missing column(s) {sorted(missing)}; "
                f"found {header}")
        # Map this system's column names onto ours, and ignore columns we were
        # not asked for rather than failing on them.
        wanted = {index: system["columns"][name]
                  for index, name in enumerate(header)
                  if name in system["columns"]}
        return [{"system": system["name"],
                 **{field: cells[index] if index < len(cells) else ""
                    for index, field in wanted.items()}}
                for cells in body]

    # -- the file ------------------------------------------------------------

    def _write(self, rows: list[dict]) -> int:
        """Merge into the CSV, keyed by (system, id), so a rerun after a
        partial failure merges instead of duplicating."""
        existing = {}
        if self.output_path.exists():
            with self.output_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames and reader.fieldnames != FIELDS:
                    # Somebody else's file: not ours to merge into.
                    raise RuntimeError(
                        f"{self.output_path} has columns {reader.fieldnames}, "
                        f"not {FIELDS} - choose another output_path")
                for row in reader:
                    existing[(row["system"], row["id"])] = row
        for row in rows:
            existing[(row["system"], row["id"])] = row

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for key in sorted(existing):
                writer.writerow({field: existing[key].get(field, "")
                                 for field in FIELDS})
        return len(existing)
