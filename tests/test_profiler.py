"""Start / Stop Profiling (#323): where the time goes, in a report that can be
attached to an issue."""

import functools
import os
import re
import sys
import threading
import time

import pytest

from keyhac.core import profiler
from keyhac.ui import tray


def _origins(tmp_path):
    return profiler.Origins(str(tmp_path / "config.py"), str(tmp_path))


def _busy(until: threading.Event) -> None:
    while not until.is_set():
        sum(range(1000))


def _capture(tmp_path, work=_busy, native=None, keys=(), limit=600, seconds=0.3):
    """A capture while the calling thread runs `work`; the folder it wrote."""
    done = threading.Event()
    seen = []
    capture = profiler.Profiler(str(tmp_path), _origins(tmp_path), interval=0.002,
                                limit=limit, native=native)
    capture.start(lambda folder, error: (seen.append((folder, error)), done.set()))
    assert capture.running
    for binding, duration in keys:
        capture.record_key(binding, duration)
    threading.Timer(seconds, capture.stop).start()
    work(done)
    assert not capture.running
    (folder, error), = seen
    assert error is None
    return folder


def _read(folder, name="summary.txt"):
    return open(os.path.join(folder, name), encoding="utf-8").read()


def test_a_capture_writes_the_main_thread_s_stacks(tmp_path):
    folder = _capture(tmp_path)
    assert os.path.dirname(folder) == str(tmp_path / "diagnostics")
    assert os.path.basename(folder).startswith("profile-")
    summary = _read(folder)
    assert "== Thread MainThread ==" in summary
    assert "_busy" in summary
    assert "CPU time of the whole process" in summary
    assert "Native stacks: not captured on this OS" in summary
    stacks = [line.rsplit(" ", 1) for line in _read(folder, "python-stacks.txt").splitlines()]
    assert any(stack.startswith("MainThread;") and "_busy" in stack and int(ms) > 0
               for stack, ms in stacks)


def test_other_threads_are_sampled_but_not_the_sampler(tmp_path):
    stop = threading.Event()

    def action_body():
        stop.wait()

    worker = threading.Thread(target=action_body, name="an-action", daemon=True)
    worker.start()
    try:
        summary = _read(_capture(tmp_path))
    finally:
        stop.set()
        worker.join()
    assert "== Thread an-action ==" in summary
    assert "action_body" in summary
    assert "keyhac-profiler" not in summary


def test_your_code_is_reported_by_function_and_line(tmp_path):
    config = tmp_path / "config.py"
    source = "def my_slow_binding(until):\n    while not until.is_set():\n        sum(range(1000))\n"
    namespace = {"__name__": "keyhac_config"}
    exec(compile(source, str(config), "exec"), namespace)
    summary = _read(_capture(tmp_path, work=namespace["my_slow_binding"]))
    yours = summary[summary.index("== Your code"):summary.index("== Key handling")]
    assert "[MainThread] my_slow_binding (config.py:1)" in yours
    assert "[MainThread] my_slow_binding (config.py:" in yours.split("By line")[1]
    assert "config.py" in summary[summary.index("By origin"):]


def test_no_code_of_yours_says_so(tmp_path):
    assert "None of it was on a stack" in _read(_capture(tmp_path))


def test_time_the_gil_was_held_is_not_lost(tmp_path):
    """The sampler cannot run during a call that holds the GIL, so counting
    samples would give that call next to nothing. Each sample carries the time
    since the last one, up to and including the one taken on stopping."""
    def holds_the_gil(until):
        # re's backtracking does not let go of the GIL within a call. Timed
        # here and not by `until`: a loop of these can starve the thread that
        # would set it for minutes.
        started = time.perf_counter()
        while time.perf_counter() - started < 0.2:
            re.match(r"(a+)+$", "a" * 20 + "b")
        until.wait()

    summary = _read(_capture(tmp_path, work=holds_the_gil, seconds=0.3))
    section = summary[summary.index("== Thread MainThread =="):]
    shares = [float(line.split()[2].rstrip("%")) for line in
              section[section.index("Innermost frames:"):].splitlines()[1:]
              if " s " in line]
    assert sum(shares) > 90


def test_key_handling_is_reported_per_binding(tmp_path):
    summary = _read(_capture(tmp_path, keys=[
        ("C-K: my_binding", 0.2), ("C-K: my_binding", 0.1), (None, 0.001)]))
    section = summary[summary.index("== Key handling"):summary.index("== Thread")]
    assert "2   300.0 ms  150.00 ms   200.0 ms  C-K: my_binding" in section
    assert "(no binding)" in section


def test_it_stops_by_itself_at_the_limit(tmp_path):
    summary = _read(_capture(tmp_path, limit=0.1, seconds=60))
    assert "Stopped by itself at the limit" in summary


def test_the_native_stacks_stop_with_the_capture(tmp_path):
    asked = []

    def native(stop, limit, path):
        asked.append(limit)
        assert stop.wait(30), "not told to stop"
        with open(path, "w") as out:
            out.write("native")

    folder = _capture(tmp_path, native=native, limit=5)
    assert asked == [5]
    assert _read(folder, "native-sample.txt") == "native"
    assert "Native stacks: native-sample.txt" in _read(folder)


def test_a_native_failure_is_said_and_the_rest_is_kept(tmp_path):
    summary = _read(_capture(tmp_path, native=lambda stop, limit, path: "not permitted"))
    assert "could not be captured: not permitted" in summary
    assert "_busy" in summary


class TestOrigins:
    """A bundle's stdlib and Keyhac are compiled on the build machine, and their
    co_filename says so - the reason these go by module name."""
    BUILD = "/Users/someone/macos_app/build/Keyhac.app/Contents/Resources"

    @pytest.mark.parametrize("filename, module, origin", [
        (f"{BUILD}/keyhac/core/keymap.py", "keyhac.core.keymap", "Keyhac"),
        (f"{BUILD}/puikit/menu.py", "puikit.menu", "PuiKit"),
        (f"{BUILD}/python/lib/python3.14/threading.py", "threading", "stdlib"),
        ("<frozen importlib._bootstrap>", "importlib._bootstrap", "stdlib"),
        (f"{BUILD}/python/lib/python3.14/site-packages/objc/_x.py", "objc._x", "packages"),
        ("/elsewhere/thing.py", "thing", "other"),
    ])
    def test_by_module(self, tmp_path, filename, module, origin):
        assert _origins(tmp_path).classify(filename, module) == origin

    def test_config_and_extensions_go_by_path(self, tmp_path):
        origins = _origins(tmp_path)
        assert origins.classify(str(tmp_path / "config.py"), "anything") == "config.py"
        assert origins.classify(str(tmp_path / "extensions" / "mine.py"), "mine") == "extensions"


class TestKeymapObserver:

    def _engine(self, engine, binding=None):
        def configure(keymap):
            if binding is not None:
                keymap.define_keytable(focus_path_pattern="*")["A"] = binding

        e = engine(configure)
        e.seen = []
        e.keymap.on_key_handled = lambda name, seconds: e.seen.append((name, seconds))
        return e

    def _names(self, e):
        return [name for name, _ in e.seen]

    def test_a_bound_function_is_named_with_its_key(self, engine):
        def my_function():
            pass
        e = self._engine(engine, my_function)
        e.down("A")
        name, seconds = e.seen[0]
        assert name.endswith(": my_function") and "A" in name
        assert seconds >= 0

    def test_an_unbound_key_is_not_named(self, engine):
        e = self._engine(engine)
        e.down("B")
        e.up("B")
        assert self._names(e) == [None, None]

    def test_a_key_sending_keys_does_not_say_which(self, engine):
        e = self._engine(engine, "B")
        e.down("A")
        assert self._names(e) == ["(a key sending keys)"]

    def test_a_partial_s_arguments_stay_out_of_it(self, engine):
        e = self._engine(engine, functools.partial(len, "my password"))
        e.down("A")
        assert "password" not in self._names(e)[0]
        assert self._names(e)[0].endswith(": partial")

    def test_the_observer_hears_when_a_handler_raises(self, engine):
        def broken():
            raise ValueError
        e = self._engine(engine, broken)
        assert e.down("A") is False  # pass-through-on-error
        assert len(e.seen) == 1

    def test_without_an_observer_a_key_still_works(self, engine):
        called = []
        e = self._engine(engine, lambda: called.append(1))
        e.keymap.on_key_handled = None
        assert e.down("A") is True
        assert called == [1] and e.seen == []


class _Keymap:
    def __init__(self, home):
        self.extensions_dir = str(home / "extensions")
        self._config_path = str(home / "config.py")
        self.app_control = self
        self.on_key_handled = None
        self.opened = []

    def call_on_main_thread(self, callback):
        callback()

    def open_url(self, url):
        self.opened.append(url)


class _Profiler:
    running = stopping = False
    limit = 600
    record_key = "the recorder"

    def start(self, on_done):
        self.running = True
        self.on_done = on_done

    def stop(self):
        self.stopping = True


class TestMenu:

    def test_start_then_stop_follows_the_capture(self, tmp_path):
        profiling = tray.Profiling(_Keymap(tmp_path), profiler=_Profiler())
        assert profiling.can_start and not profiling.can_stop
        profiling.start()
        assert not profiling.can_start and profiling.can_stop
        profiling.stop()
        assert not profiling.can_stop  # still writing

    def test_keys_are_timed_only_during_the_capture(self, tmp_path):
        keymap = _Keymap(tmp_path)
        fake = _Profiler()
        profiling = tray.Profiling(keymap, profiler=fake)
        profiling.start()
        assert keymap.on_key_handled == "the recorder"
        profiling.stop()
        assert keymap.on_key_handled is None

    def test_reaching_the_limit_also_stops_timing_and_opens_the_folder(self, tmp_path):
        keymap = _Keymap(tmp_path)
        fake = _Profiler()
        tray.Profiling(keymap, profiler=fake).start()
        fake.on_done("/data/diagnostics/profile-now", None)
        assert keymap.on_key_handled is None
        assert keymap.opened == ["/data/diagnostics/profile-now"]

    def test_the_real_profiler_writes_beside_the_config(self, tmp_path):
        profiling = tray.Profiling(_Keymap(tmp_path))
        assert profiling._profiler._data_dir == str(tmp_path)
        assert profiling.can_start


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_sample_runs_on_this_process_and_stops_when_told(tmp_path):
    """The real /usr/bin/sample: interrupted, it still writes its report."""
    from keyhac.platform.mac.diagnostics import sample_native
    stop = threading.Event()
    path = tmp_path / "native.txt"
    threading.Timer(1.5, stop.set).start()
    started = time.monotonic()
    assert sample_native(stop, 60, str(path)) is None
    assert time.monotonic() - started < 45
    assert "Call graph" in path.read_text()
