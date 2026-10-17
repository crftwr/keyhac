"""Cross-system extraction: read two systems that disagree, write one CSV.

Windows, Microsoft Edge. The same task as `mac/extract_records.py`, against
the same fixture pages; read the two side by side for what a change of
platform costs. The pipeline is line for line the same:

    enumerate targets -> for each: navigate, wait, read, accumulate
                      -> normalise -> emit in an external format

What is not the same is every selector that touches a table. Windows has no
Row or Cell role: Chromium reports a table's rows and its cells alike as
`DataItem`, so they are told apart by where they sit - a row is a child of
the table, a cell is a child of a row - rather than by what they call
themselves.

    python tools/run_action_file.py examples/actions/win/extract_records.py
        [output_path=~/Desktop/records.csv]

WHAT IT IS BUILT TO SURVIVE
  - A page that has not arrived yet. Next is a `ui.click` whose `until=` names
    the page that must come up ("page 2 of 3"). On Windows that also covers
    Chromium building its accessibility tree late the first time a client
    attaches: a wait that names its target is a retry for free.
  - A page that fails. One bad system does not lose the pages already read.
  - Being run twice. Rows are keyed, so a rerun merges rather than duplicates.
"""

import csv
import pathlib
import re
import subprocess

from keyhac import ThreadedAction, getLogger

logger = getLogger("ExtractRecords")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

#: The systems to read, and what each calls the three columns we want.
SYSTEMS = [
    {"name": "SystemA",
     "url": (FIXTURES / "systema_1.html").as_uri(),
     "columns": {"ID": "id", "Name": "name", "Amount": "amount"}},
    {"name": "SystemB",
     "url": (FIXTURES / "systemb_1.html").as_uri(),
     "columns": {"Reference": "id", "Title": "name", "Value": "amount"}},
]

FIELDS = ["system", "id", "name", "amount"]

PAGE_LABEL = re.compile(r"page (\d+) of (\d+)")


class ChangedScreen(RuntimeError):
    """The page is not the one this action was written against - regenerate
    it. Kept apart from a step that merely failed, which a rerun fixes."""


class ExtractRecords(ThreadedAction):
    """Walk every page of every system and write one normalised CSV."""

    def __init__(self, systems=SYSTEMS, output_path="~/Desktop/records.csv",
                 app_name="msedge", page_limit=50):
        self.systems = systems
        self.output_path = pathlib.Path(output_path).expanduser()
        self.app_name = app_name
        self.page_limit = page_limit
        self.done: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def starting(self):
        logger.info(f"extracting {len(self.systems)} systems "
                    f"-> {self.output_path}")

    def run(self):
        ui = self.ui
        # Chromium gives every element with a DOM id an AutomationId, so the
        # table is addressed by its id. The page label is a plain <span>: a
        # Text element whose words are its name, matched by what it says.
        self.TABLE = ui.Locator(role="Table", identifier="results")
        self.NEXT = ui.Locator(role="Hyperlink", name="Next")

        rows: list[dict] = []
        for system in self.systems:
            try:
                self._read_system(system, rows)
            except Exception as error:
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
        # `start` resolves msedge through App Paths; the empty string is the
        # window title `start` would otherwise take the first argument for.
        subprocess.run(["cmd", "/c", "start", "", self.app_name, system["url"]],
                       check=True, capture_output=True, text=True)
        # Edge's title is "<page title> - <profile> - Microsoft Edge", so the
        # pattern ends in a star. Chosen by what it shows, then pinned by
        # identity, because the page title changes on every page.
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
            ui.click(self.NEXT, within=window, given=front,
                     until=ui.Appears(
                         ui.Locator(role="Text",
                                    text=f"page {page + 1} of {last}*"),
                         within=window),
                     timeout=20)
        raise RuntimeError(f"more than {self.page_limit} pages; "
                           f"is 'Next' linking to itself?")

    def _page_number(self, window) -> tuple[int, int]:
        label = window.find(role="Text", text="page * of *")
        match = PAGE_LABEL.search(label.text if label else "")
        if match is None:
            raise ChangedScreen("no 'page N of M' label on the page")
        return int(match.group(1)), int(match.group(2))

    def _read_page(self, window, system) -> list[dict]:
        table = window.find(**self.TABLE.criteria())
        if table is None:
            raise ChangedScreen(f"{system['name']} has no #results table")
        grid = [[cell.all_text.strip() for cell in row.children]
                for row in self._rows(table.reread(max_depth=4))]
        if not grid:
            return []

        header, *body = grid
        missing = set(system["columns"]) - set(header)
        if missing:
            raise ChangedScreen(
                f"{system['name']} is missing column(s) {sorted(missing)}; "
                f"found {header}")
        wanted = {index: system["columns"][name]
                  for index, name in enumerate(header)
                  if name in system["columns"]}
        return [{"system": system["name"],
                 **{field: cells[index] if index < len(cells) else ""
                    for index, field in wanted.items()}}
                for cells in body]

    @staticmethod
    def _rows(table) -> list:
        """The table's rows, by position: its children, looking through a
        row group (<thead>, <tbody>) where Chromium reports one as a Group.

        Not `find_all(role="DataItem")`, which returns every cell as well as
        every row - Chromium gives both the same control type.
        """
        rows = []
        for child in table.children:
            if child.role == "Group":
                rows += [row for row in child.children if row.children]
            elif child.children:
                rows.append(child)
        return rows

    # -- the file ------------------------------------------------------------

    def _write(self, rows: list[dict]) -> int:
        """Merge into the CSV, keyed by (system, id)."""
        existing = {}
        if self.output_path.exists():
            with self.output_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames and reader.fieldnames != FIELDS:
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
