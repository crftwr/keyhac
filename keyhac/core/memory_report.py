"""Advanced > Write Memory Report: for what accumulates in a Keyhac that has
been running for weeks (#324).

Accumulation is noticed long after it starts, so the history has to exist
already by then: a worker thread records the process total and Python's
allocated blocks every few minutes from startup, and a census of objects by type
is taken once, shortly after startup, to compare later ones against.

A report then answers, in order: is it growing and since when; is it Python or
native (the two totals, side by side, plus the platform's own breakdown); and if
Python, of what type and from where (the census, and tracemalloc if switched on).

All of it runs on a worker thread, and the main thread runs the keyboard hook.
Measured with 300k objects, longest stall of a 1 ms heartbeat: census 7.5 ms,
gc.collect 24 ms, tracemalloc.take_snapshot 62 ms per 100k live allocations.
The last two hold the GIL throughout; there is no way round that.

The reports get attached to issues, so they hold counts, sizes and code
locations: never clipboard contents, console text or window titles.
"""

import collections
import gc
import linecache
import os
import sys
import threading
import time
import tracemalloc

from keyhac.core import diagnostics, log
from keyhac.platform.base import ProcessMemory

logger = log.getLogger("Memory")

#: Between two rows of the history.
HISTORY_INTERVAL = 600

#: Before the census later ones compare against: the config has loaded by then.
BASELINE_DELAY = 60

#: Rows kept: a week. The first row is kept beyond that, so that a long run
#: still shows where it started.
HISTORY_ROWS = 1008

#: Frames per allocation when tracking. One says only which line allocated; the
#: callers are what tell a leak's source from a helper it went through.
TRACEMALLOC_FRAMES = 10

_TOP = 30
_TOP_TRACEBACKS = 10
_SUMMARY_ROWS = 24
_NATIVE_LINES = 20

_MB = 1024 * 1024


def _size(n: float) -> str:
    return f"{n / _MB:,.1f} MB"


def _signed_size(n: float) -> str:
    return f"{n / _MB:+,.2f} MB"


def _census() -> dict:
    """{type name: (objects, bytes)} over what gc tracks. That leaves out what
    gc has no reason to track - str, bytes, numbers - which only tracemalloc
    and the allocated blocks can see."""
    counts = collections.Counter()
    sizes = collections.Counter()
    for obj in gc.get_objects():
        kind = type(obj)
        counts[kind] += 1
        try:
            sizes[kind] += sys.getsizeof(obj)
        except Exception:   # a __sizeof__ of someone else's
            pass
    census = {}
    for kind, count in counts.items():
        try:
            name = f"{kind.__module__}.{kind.__qualname__}"
        except Exception:
            name = repr(kind)
        objects, size = census.get(name, (0, 0))
        census[name] = (objects + count, size + sizes[kind])
    return census


def _growth(now: dict, then: dict) -> list:
    """[(name, objects, bytes)] of the types that grew, the most bytes first."""
    grown = []
    for name, (objects, size) in now.items():
        old_objects, old_size = then.get(name, (0, 0))
        if objects > old_objects or size > old_size:
            grown.append((name, objects - old_objects, size - old_size))
    grown.sort(key=lambda row: (-row[2], -row[1]))
    return grown


def _short_path(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def keyhac_stores(keymap) -> list:
    """What Keyhac itself holds on to, as (name, callable returning a text).
    Each is bounded by design; a number here far from its bound is the finding."""
    from keyhac.core import capture
    from keyhac.core.action import ThreadedAction

    def clipboard() -> str:
        history = keymap.clipboard_history
        if history is None:
            return "none"
        entries = list(history.entries())
        rich = sum(entry.snapshot.nbytes() for entry in entries)
        text = sum(len(entry.snapshot.text or "") for entry in entries)
        return (f"{len(entries)} entries of at most {history.max_items}, "
                f"{text:,} characters of text, {_size(rich)} of rich content "
                f"of at most {_size(history.max_session_data_size)}")

    def console() -> str:
        lines = log.Console.get_instance().lines()
        return (f"{len(lines)} lines of at most {log.Console.max_lines}, "
                f"{sum(len(text) for text, _ in lines):,} characters")

    return [
        ("Clipboard history", clipboard),
        ("Console", console),
        ("Action run records", lambda: f"{len(capture._runs)}, one per action"),
        ("Actions running", lambda: str(len(ThreadedAction._running))),
        ("Key tables", lambda: str(len(keymap._all_keytables))),
        ("Python modules loaded", lambda: str(len(sys.modules))),
    ]


class MemoryReporter:
    """The history, and one report at a time into
    ``<data_dir>/diagnostics/memory-<time>/``:

    - ``summary.txt``: the part to read
    - ``types.txt``: every type in the census
    - ``history.csv``: every row of the history
    - ``native-footprint.txt``: the platform's own breakdown, where it has one
    - ``allocations.txt``: while allocations are being tracked

    ``on_done(folder, error)`` is called from the worker thread when the report
    is written; the caller takes it to the main thread.
    """

    def __init__(self, data_dir: str, process_memory, native_report=None, stores=(),
                 interval: float = HISTORY_INTERVAL,
                 baseline_delay: float = BASELINE_DELAY):
        self._data_dir = data_dir
        self._process_memory = process_memory
        self._native_report = native_report
        self._stores = stores
        self._interval, self._baseline_delay = interval, baseline_delay
        self._lock = threading.Lock()   # the history and the censuses
        self._first_row = None
        self._rows = collections.deque(maxlen=HISTORY_ROWS)
        self._baseline = None           # (monotonic time, census)
        self._previous = None           # the same, of the last report
        self._previous_snapshot = None  # tracemalloc, of the last report
        self._closed = threading.Event()
        self._tracking_since = None
        self.running = False

    # -- the history -----------------------------------------------------

    def start_history(self) -> None:
        threading.Thread(target=self._record_history,
                         name="keyhac-memory-history", daemon=True).start()

    def close(self) -> None:
        self._closed.set()

    def _record_history(self) -> None:
        self._record_row()
        if self._closed.wait(self._baseline_delay):
            return
        census = _census()
        with self._lock:
            if self._baseline is None:
                self._baseline = (time.monotonic(), census)
        self._record_row()
        while not self._closed.wait(self._interval):
            self._record_row()

    def _record_row(self) -> None:
        try:
            row = (time.time(), self._process_memory().used, sys.getallocatedblocks())
        except Exception:
            logger.exception("Could not read the process's memory")
            return
        with self._lock:
            if self._first_row is None:
                self._first_row = row
            self._rows.append(row)

    def _history(self) -> list:
        with self._lock:
            rows = list(self._rows)
            if self._first_row is not None and (not rows or rows[0] != self._first_row):
                rows.insert(0, self._first_row)
        return rows

    # -- tracking --------------------------------------------------------

    @property
    def tracking(self) -> bool:
        return tracemalloc.is_tracing()

    def set_tracking(self, on: bool) -> None:
        """tracemalloc sees only what is allocated after it starts, and costs
        CPU on every allocation and memory for every one still alive."""
        if on and not tracemalloc.is_tracing():
            tracemalloc.start(TRACEMALLOC_FRAMES)
            self._tracking_since = time.monotonic()
        elif not on and tracemalloc.is_tracing():
            tracemalloc.stop()
            self._tracking_since = None
        self._previous_snapshot = None

    # -- the report ------------------------------------------------------

    def write(self, on_done) -> None:
        if self.running:
            logger.info("A memory report is already being written.")
            return
        self.running = True
        folder = diagnostics.new_folder(self._data_dir, "memory")

        def worker() -> None:
            try:
                os.makedirs(folder, exist_ok=True)
                self._write(folder)
                error = None
            except Exception as e:
                logger.exception("Memory report failed")
                error = e
            self.running = False
            on_done(folder, error)

        threading.Thread(target=worker, name="keyhac-memory-report", daemon=True).start()

    def _write(self, folder: str) -> None:
        # First, so that what is merely waiting for a collection is not
        # reported as having grown.
        collected = gc.collect()
        census = _census()
        now = time.monotonic()
        with self._lock:
            if self._baseline is None:
                self._baseline = (now, census)
            baseline, previous = self._baseline, self._previous
            self._previous = (now, census)
        self._record_row()
        process = self._process_memory()

        native = None
        if self._native_report is not None:
            native = self._read_native(os.path.join(folder, "native-footprint.txt"))
        allocations = self._write_allocations(folder)
        self._write_types(folder, census, baseline[1])
        rows = self._history()
        self._write_history(folder, rows)

        lines = diagnostics.header("Keyhac memory report")
        lines += self._reading_guide()
        lines += self._process_section(process, rows)
        lines += self._python_section(census, collected)
        lines += self._stores_section()
        if native is not None:
            lines += ["", "== Native (native-footprint.txt) ==", "", *native]
        if baseline[0] != now:
            lines += self._growth_section("since shortly after startup", census, baseline, now)
        if previous is not None:
            lines += self._growth_section("since the previous report", census, previous, now)
        lines += self._largest_section(census)
        lines += ["", "== Allocations ==", "", *allocations]
        with open(os.path.join(folder, "summary.txt"), "w", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")

    @staticmethod
    def _reading_guide() -> list:
        return [
            "",
            "If the process total grows and Python's allocated blocks do not, the",
            "memory is native: look at Native. If both grow, it is Python: look at the",
            "types that grew, and for what they cannot show, switch on Advanced > Track",
            "Python Allocations and write a report a while later. Two reports some",
            "hours apart show more than one: the first is what the second compares to.",
        ]

    def _process_section(self, process: ProcessMemory, rows: list) -> list:
        lines = ["", "== Process ==", ""]
        lines += [f"{label}: {value}" for label, value in process.details.items()]
        names = sorted(thread.name for thread in threading.enumerate())
        lines.append(f"Python threads: {len(names)} ({', '.join(names)})")
        lines += ["", "History, every "
                  f"{self._interval / 60:g} minutes (all of it in history.csv):", ""]
        lines.append(f"{'':19}  {'process':>12}  {'Python blocks':>13}")
        shown = rows
        if len(rows) > _SUMMARY_ROWS:
            step = (len(rows) - 1) / (_SUMMARY_ROWS - 1)
            shown = [rows[round(i * step)] for i in range(_SUMMARY_ROWS)]
        for when, used, blocks in shown:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
            lines.append(f"{stamp}  {_size(used):>12}  {blocks:13,d}")
        if len(rows) >= 2:
            lines += ["", f"Since the first row: {_signed_size(rows[-1][1] - rows[0][1])} "
                          f"in the process, {rows[-1][2] - rows[0][2]:+,d} Python blocks."]
        return lines

    @staticmethod
    def _python_section(census: dict, collected: int) -> list:
        objects = sum(count for count, _ in census.values())
        size = sum(size for _, size in census.values())
        lines = [
            "", "== Python ==", "",
            f"Allocated blocks: {sys.getallocatedblocks():,}",
            f"Objects gc tracks: {objects:,}, {_size(size)} by sys.getsizeof. That leaves",
            "out what gc does not track (str, bytes, numbers) and what objects own",
            "indirectly.",
            f"Collected by a full collection just before this: {collected:,}",
            f"gc.garbage (uncollectable, and kept): {len(gc.garbage):,}",
        ]
        for generation, stats in enumerate(gc.get_stats()):
            lines.append(
                f"gc generation {generation}: {stats['collections']:,} collections, "
                f"{stats['collected']:,} collected, {stats['uncollectable']:,} uncollectable")
        return lines

    def _stores_section(self) -> list:
        lines = ["", "== What Keyhac holds on to ==", ""]
        for name, read in self._stores:
            try:
                lines.append(f"{name}: {read()}")
            except Exception as e:
                lines.append(f"{name}: could not be read ({e!r})")
        return lines

    def _read_native(self, path: str) -> list:
        error = self._native_report(path)
        if error is not None:
            return [f"Could not be captured: {error}"]
        with open(path, encoding="utf-8", errors="replace") as source:
            return [line.rstrip() for line in source][:_NATIVE_LINES]

    @staticmethod
    def _growth_section(since: str, census: dict, then: tuple, now: float) -> list:
        lines = ["", f"== Types that grew {since} ==", "",
                 f"Over {(now - then[0]) / 3600:.1f} hours, most bytes first:", ""]
        grown = _growth(census, then[1])
        if not grown:
            return lines + ["None."]
        lines.append(f"{'objects':>10}  {'bytes':>12}  type")
        lines += [f"{objects:+10,d}  {_signed_size(size):>12}  {name}"
                  for name, objects, size in grown[:_TOP]]
        return lines

    @staticmethod
    def _largest_section(census: dict) -> list:
        lines = ["", "== Largest types ==", "", f"{'objects':>10}  {'size':>12}  type"]
        largest = sorted(census.items(), key=lambda item: -item[1][1])
        lines += [f"{count:10,d}  {_size(size):>12}  {name}"
                  for name, (count, size) in largest[:_TOP]]
        return lines

    @staticmethod
    def _write_types(folder: str, census: dict, baseline: dict) -> None:
        with open(os.path.join(folder, "types.txt"), "w", encoding="utf-8") as out:
            out.write(f"{'objects':>10} {'bytes':>14} {'+objects':>10} {'+bytes':>14}  type\n")
            for name, (count, size) in sorted(census.items(), key=lambda i: -i[1][1]):
                old_count, old_size = baseline.get(name, (0, 0))
                out.write(f"{count:10d} {size:14d} {count - old_count:+10d} "
                          f"{size - old_size:+14d}  {name}\n")

    @staticmethod
    def _write_history(folder: str, rows: list) -> None:
        with open(os.path.join(folder, "history.csv"), "w", encoding="utf-8") as out:
            out.write("time,process_bytes,python_blocks\n")
            for when, used, blocks in rows:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(when))
                out.write(f"{stamp},{used},{blocks}\n")

    def _write_allocations(self, folder: str) -> list:
        """allocations.txt, and what the summary says about it."""
        if not tracemalloc.is_tracing():
            return ["Not tracked. Advanced > Track Python Allocations switches it on."]
        snapshot = tracemalloc.take_snapshot().filter_traces((
            tracemalloc.Filter(False, tracemalloc.__file__),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
        ))
        traced, peak = tracemalloc.get_traced_memory()
        overhead = tracemalloc.get_tracemalloc_memory()
        since = ""
        if self._tracking_since is not None:
            since = f", for {(time.monotonic() - self._tracking_since) / 3600:.1f} hours"
        lines = [
            f"Python allocations still alive that were made while tracking{since}:",
            f"{_size(traced)} (peak {_size(peak)}). Tracking itself holds {_size(overhead)}.",
            "",
            "By line:", "",
        ]
        lines += self._allocation_rows(snapshot.statistics("lineno")[:_TOP])
        previous, self._previous_snapshot = self._previous_snapshot, snapshot
        if previous is not None:
            lines += ["", "Growth since the previous report, by line:", ""]
            for stat in snapshot.compare_to(previous, "lineno")[:_TOP]:
                frame = stat.traceback[0]
                lines.append(f"{_signed_size(stat.size_diff):>12}  {stat.count_diff:+9,d} blocks  "
                             f"{_short_path(frame.filename)}:{frame.lineno}")
        summary = list(lines)
        lines += ["", "The largest, with who called them (most recent call last):"]
        for stat in snapshot.statistics("traceback")[:_TOP_TRACEBACKS]:
            lines += ["", f"{_size(stat.size)} in {stat.count:,} blocks"]
            for frame in stat.traceback:
                source = linecache.getline(frame.filename, frame.lineno).strip()
                lines.append(f"    {_short_path(frame.filename)}:{frame.lineno}  {source}")
        with open(os.path.join(folder, "allocations.txt"), "w", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
        return summary + ["", "With their callers: allocations.txt"]

    @staticmethod
    def _allocation_rows(stats: list) -> list:
        return [f"{_size(stat.size):>12}  {stat.count:9,d} blocks  "
                f"{_short_path(stat.traceback[0].filename)}:{stat.traceback[0].lineno}"
                for stat in stats]
