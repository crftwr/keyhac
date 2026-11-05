"""Write Memory Report (#324): is it growing, is it Python or native, and of
what - in a report that can be attached to an issue."""

import gc
import os
import subprocess
import sys
import threading
import tracemalloc

import pytest

from keyhac.core import memory_report
from keyhac.core.memory_report import MemoryReporter
from keyhac.platform.base import ProcessMemory
from keyhac.ui import tray

_MB = 1024 * 1024


class _Process:
    """A process total that goes where the test says."""

    def __init__(self):
        self.used = 100 * _MB

    def __call__(self):
        return ProcessMemory(self.used, {"Physical footprint": f"{self.used} bytes"})


class AccumulatingThing:
    pass


def _reporter(tmp_path, **options):
    return MemoryReporter(str(tmp_path), options.pop("process", _Process()), **options)


def _write(reporter):
    done = threading.Event()
    seen = []
    reporter.write(lambda folder, error: (seen.append((folder, error)), done.set()))
    assert done.wait(60)
    assert not reporter.running
    (folder, error), = seen
    assert error is None
    return folder


def _read(folder, name="summary.txt"):
    return open(os.path.join(folder, name), encoding="utf-8").read()


def test_a_report_has_the_process_and_python(tmp_path):
    folder = _write(_reporter(tmp_path))
    assert os.path.dirname(folder) == str(tmp_path / "diagnostics")
    assert os.path.basename(folder).startswith("memory-")
    summary = _read(folder)
    assert f"Physical footprint: {100 * _MB} bytes" in summary
    assert "Allocated blocks:" in summary
    assert "Objects gc tracks:" in summary
    assert "== Largest types ==" in summary
    assert "builtins.dict" in _read(folder, "types.txt")


def test_the_second_report_shows_what_grew_since_the_first(tmp_path):
    reporter = _reporter(tmp_path)
    _write(reporter)
    kept = [AccumulatingThing() for _ in range(5000)]
    summary = _read(_write(reporter))
    section = summary[summary.index("since the previous report"):]
    line = next(l for l in section.splitlines() if "AccumulatingThing" in l)
    assert line.split()[0] == "+5,000"
    assert "+5000" in next(l for l in _read(_write(reporter), "types.txt").splitlines()
                           if "AccumulatingThing" in l)
    del kept


def test_garbage_is_collected_before_it_can_read_as_growth(tmp_path):
    reporter = _reporter(tmp_path)
    _write(reporter)
    gc.disable()
    try:
        for _ in range(5000):
            thing = AccumulatingThing()
            thing.me = thing
        del thing
        summary = _read(_write(reporter))
    finally:
        gc.enable()
    assert "AccumulatingThing" not in summary


class TestHistory:

    def _run(self, reporter, rows):
        reporter.start_history()
        deadline = threading.Event()
        for _ in range(500):
            if len(reporter._rows) >= rows:
                break
            deadline.wait(0.01)
        reporter.close()
        assert len(reporter._rows) >= rows

    def test_it_is_recording_before_anyone_asks(self, tmp_path):
        process = _Process()
        reporter = _reporter(tmp_path, process=process, interval=0.01, baseline_delay=0.01)
        self._run(reporter, 3)
        process.used += 50 * _MB
        folder = _write(reporter)
        assert "Since the first row: +50.00 MB" in _read(folder)
        assert _read(folder, "history.csv").startswith("time,process_bytes,python_blocks\n")

    def test_the_baseline_is_from_shortly_after_startup(self, tmp_path):
        reporter = _reporter(tmp_path, interval=0.01, baseline_delay=0.01)
        self._run(reporter, 2)
        kept = [AccumulatingThing() for _ in range(5000)]
        summary = _read(_write(reporter))
        section = summary[summary.index("since shortly after startup"):]
        assert "AccumulatingThing" in section
        del kept

    def test_a_long_run_keeps_its_first_row(self, tmp_path):
        process = _Process()
        reporter = _reporter(tmp_path, process=process)
        reporter._record_row()
        first = reporter._first_row
        process.used = 200 * _MB
        for _ in range(memory_report.HISTORY_ROWS + 5):
            reporter._record_row()
        assert reporter._rows[0] != first and reporter._history()[0] == first

    def test_a_long_history_is_thinned_in_the_summary_only(self, tmp_path):
        reporter = _reporter(tmp_path)
        for _ in range(100):
            reporter._record_row()
        folder = _write(reporter)
        assert len(_read(folder, "history.csv").splitlines()) >= 101
        history = _read(folder).split("History, every")[1].split("Since the first")[0]
        assert sum(":" in line and "MB" in line for line in history.splitlines()) == 24

    def test_a_failing_read_does_not_end_it(self, tmp_path):
        def broken():
            raise OSError("no")
        reporter = _reporter(tmp_path, process=broken)
        reporter._record_row()
        assert reporter._history() == []


class TestNative:

    def test_it_is_written_and_its_head_summarized(self, tmp_path):
        def native(path):
            with open(path, "w") as out:
                out.write("Footprint: 795 MB\n523 MB CoreAnimation\n")

        folder = _write(_reporter(tmp_path, native_report=native))
        assert "523 MB CoreAnimation" in _read(folder)
        assert "523 MB CoreAnimation" in _read(folder, "native-footprint.txt")

    def test_a_failure_is_said_and_the_rest_is_kept(self, tmp_path):
        summary = _read(_write(_reporter(tmp_path, native_report=lambda path: "not permitted")))
        assert "Could not be captured: not permitted" in summary
        assert "== Python ==" in summary


class TestStores:

    def test_a_broken_one_does_not_stop_the_report(self, tmp_path):
        def broken():
            raise RuntimeError("changed size during iteration")
        summary = _read(_write(_reporter(tmp_path, stores=[
            ("Broken", broken), ("Fine", lambda: "12")])))
        assert "Broken: could not be read" in summary
        assert "Fine: 12" in summary

    def test_keyhac_s_own_are_counts_and_not_contents(self, tmp_path, engine):
        from keyhac.core.clipboard_history import ClipboardHistory
        from keyhac.core import log
        from tests.test_clipboard import FakeProvider

        e = engine(lambda keymap: None)
        history = ClipboardHistory(FakeProvider(), str(tmp_path / "c.json"))
        history.add_item("my secret clipboard text")
        e.keymap._clipboard_history = history
        log.Console.get_instance().write("my secret console line\n")

        summary = _read(_write(_reporter(tmp_path, stores=memory_report.keyhac_stores(e.keymap))))
        assert "Clipboard history: 1 entries of at most 1000, 24 characters" in summary
        assert "Console: " in summary
        assert "Key tables: " in summary
        assert "secret" not in summary


class TestTracking:

    @pytest.fixture(autouse=True)
    def stop_tracking(self):
        yield
        if tracemalloc.is_tracing():
            tracemalloc.stop()

    def test_off_says_how_to_switch_it_on(self, tmp_path):
        folder = _write(_reporter(tmp_path))
        assert "Advanced > Track Python Allocations switches it on" in _read(folder)
        assert not os.path.exists(os.path.join(folder, "allocations.txt"))

    def test_on_says_who_allocated_what_is_alive(self, tmp_path):
        reporter = _reporter(tmp_path)
        reporter.set_tracking(True)
        assert reporter.tracking
        kept = [bytearray(1024) for _ in range(5000)]
        folder = _write(reporter)
        assert "test_memory_report.py:" in _read(folder)
        allocations = _read(folder, "allocations.txt")
        assert "test_memory_report.py:" in allocations
        assert "bytearray(1024)" in allocations     # with the source line
        assert os.path.expanduser("~") + os.sep not in allocations
        del kept

    def test_the_second_report_shows_the_growth_by_line(self, tmp_path):
        reporter = _reporter(tmp_path)
        reporter.set_tracking(True)
        _write(reporter)
        kept = [bytearray(1024) for _ in range(5000)]
        summary = _read(_write(reporter))
        section = summary[summary.index("Growth since the previous report"):]
        assert "test_memory_report.py:" in section.splitlines()[2]
        del kept

    def test_off_stops_it(self, tmp_path):
        reporter = _reporter(tmp_path)
        reporter.set_tracking(True)
        reporter.set_tracking(False)
        assert not tracemalloc.is_tracing() and not reporter.tracking


class _Keymap:
    def __init__(self, home):
        self.extensions_dir = str(home / "extensions")
        self.app_control = self
        self.clipboard_history = None
        self._all_keytables = []
        self.opened = []

    def call_on_main_thread(self, callback):
        callback()

    def open_url(self, url):
        self.opened.append(url)


class TestMenu:

    def test_the_folder_is_opened_when_written(self, tmp_path):
        keymap = _Keymap(tmp_path)
        reports = tray.MemoryReports(keymap, reporter=_reporter(tmp_path))
        reports.write()
        for thread in threading.enumerate():
            if thread.name == "keyhac-memory-report":
                thread.join(60)
        assert len(keymap.opened) == 1
        assert os.path.exists(os.path.join(keymap.opened[0], "summary.txt"))

    def test_tracking_toggles(self, tmp_path):
        reports = tray.MemoryReports(_Keymap(tmp_path), reporter=_reporter(tmp_path))
        reports.toggle_tracking()
        assert reports.tracking
        reports.toggle_tracking()
        assert not reports.tracking

    def test_the_real_reporter_writes_beside_the_config(self, tmp_path):
        reports = tray.MemoryReports(_Keymap(tmp_path))
        assert reports._reporter._data_dir == str(tmp_path)


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="macOS and Windows")
def test_the_real_process_memory():
    from keyhac import platform
    process = platform.process_memory()
    assert process.used > 1024 * 1024
    assert process.details


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_the_real_footprint(tmp_path):
    from keyhac.platform.mac.diagnostics import process_memory, write_footprint
    path = tmp_path / "footprint.txt"
    assert write_footprint(str(path)) is None
    report = path.read_text()
    assert "phys_footprint" in report
    # The number the history records agrees with the system tool's: this is
    # what says the hand-written rusage_info layout is right.
    footprint_mb = int(next(line for line in report.splitlines()
                            if "Footprint:" in line).split("Footprint:")[1].split()[0])
    assert abs(process_memory().used / _MB - footprint_mb) < max(20, footprint_mb * 0.3)


class TestTheMenuInTheTray:
    """install_tray itself: the items as the menu holds them, doing the real
    thing on this OS (the system tools included)."""

    class _Backend:
        def __init__(self):
            self.menu = None

        def show_main_window(self):
            pass

        def quit(self):
            pass

        def set_tray(self, title, menu, tooltip=None):
            self.menu = menu

    class _Console:
        def __init__(self, backend):
            self.backend = backend
            self.state_listeners = []

        def mcp_timeout_disabled(self):
            return False

    class _Hook:
        installed = True

    def _advanced(self, tmp_path, keymap):
        backend = self._Backend()
        tray.install_tray(self._Console(backend), keymap, self._Hook())
        advanced = next(i for i in backend.menu.items if getattr(i, "label", "") == "Advanced")
        return {i.label: i for i in advanced.submenu.items if hasattr(i, "label")}

    @pytest.fixture
    def keymap(self, tmp_path):
        keymap = _Keymap(tmp_path)
        keymap._config_path = str(tmp_path / "config.py")
        keymap.mcp_server_running = False
        keymap.edit_config = keymap.configure = lambda: None
        keymap.on_key_handled = None
        return keymap

    def _wait(self, name):
        for thread in threading.enumerate():
            if thread.name == name:
                thread.join(90)

    def test_profiling(self, tmp_path, keymap):
        items = self._advanced(tmp_path, keymap)
        start, stop = items["Start Profiling"], items["Stop Profiling"]
        assert start.is_enabled() and not stop.is_enabled()
        start.activate()
        assert not start.is_enabled() and stop.is_enabled()
        assert keymap.on_key_handled is not None
        threading.Event().wait(1.2)
        stop.activate()
        assert not stop.is_enabled()
        self._wait("keyhac-profiler")
        assert start.is_enabled()
        folder, = keymap.opened
        assert os.path.basename(folder).startswith("profile-")
        assert "== Thread MainThread ==" in _read(folder)
        if sys.platform == "darwin":
            assert "Native stacks: native-sample.txt" in _read(folder)
            assert os.path.getsize(os.path.join(folder, "native-sample.txt")) > 0

    def test_memory(self, tmp_path, keymap):
        items = self._advanced(tmp_path, keymap)
        items["Write Memory Report"].activate()
        self._wait("keyhac-memory-report")
        folder, = keymap.opened
        assert os.path.basename(folder).startswith("memory-")
        assert "Key tables: 0" in _read(folder)
        if sys.platform == "darwin":
            assert "CATEGORY" in _read(folder).upper()

    def test_tracking(self, tmp_path, keymap):
        item = self._advanced(tmp_path, keymap)["Track Python Allocations"]
        assert not item.is_checked()
        item.activate()
        try:
            assert item.is_checked()
        finally:
            item.activate()
        assert not item.is_checked()


@pytest.mark.skipif(sys.platform != "darwin", reason="PyObjC")
def test_a_census_does_not_destroy_another_thread_s_objects():
    """gc.get_objects() must not become the last reference to something the main
    thread lets go of: an NSAutoreleasePool destroyed on a worker segfaults."""
    script = """
import threading, time, objc
from keyhac.core import memory_report
stop = threading.Event()
def worker():
    while not stop.is_set():
        memory_report._census()
thread = threading.Thread(target=worker, daemon=True)
thread.start()
end = time.monotonic() + 3
while time.monotonic() < end:
    with objc.autorelease_pool():
        pass
stop.set()
thread.join(10)
"""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr.decode()[-2000:]


def test_a_census_puts_the_switch_interval_back():
    before = sys.getswitchinterval()
    memory_report._census()
    assert sys.getswitchinterval() == before
