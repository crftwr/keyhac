"""Capture Sampling Data (#292): the main thread's stacks over a short
capture, written where the user can attach them."""

import os
import threading

from keyhac.core import sampling
from keyhac.ui import tray


def _busy_in_the_main_thread(until: threading.Event) -> None:
    while not until.is_set():
        sum(range(1000))


def _capture(tmp_path, native=None):
    done = threading.Event()
    seen = []
    capture = sampling.Capture(str(tmp_path), seconds=0.3, interval=0.002, native=native)
    capture.start(lambda folder, error: (seen.append((folder, error)), done.set()))
    assert capture.running
    _busy_in_the_main_thread(done)
    assert not capture.running
    (folder, error), = seen
    assert error is None
    return folder


def test_the_main_thread_s_stacks_are_written(tmp_path):
    folder = _capture(tmp_path)
    assert os.path.dirname(folder) == str(tmp_path / "performance")
    summary = open(os.path.join(folder, "summary.txt"), encoding="utf-8").read()
    assert "_busy_in_the_main_thread" in summary
    assert "CPU time of the whole process" in summary
    assert "Native stacks: not captured on this OS" in summary
    folded = open(os.path.join(folder, "python-stacks.txt"), encoding="utf-8").read()
    stack, count = folded.splitlines()[0].rsplit(" ", 1)
    assert "_busy_in_the_main_thread" in stack and int(count) > 0


def test_the_native_stacks_are_captured_over_the_same_time(tmp_path):
    asked = []

    def native(seconds, path):
        asked.append(seconds)
        with open(path, "w") as out:
            out.write("native")
        return None

    folder = _capture(tmp_path, native)
    assert asked == [0.3]
    assert open(os.path.join(folder, "native-sample.txt")).read() == "native"
    assert "Native stacks: native-sample.txt" in open(
        os.path.join(folder, "summary.txt"), encoding="utf-8").read()


def test_a_native_failure_is_said_and_the_rest_is_kept(tmp_path):
    folder = _capture(tmp_path, lambda seconds, path: "not permitted")
    summary = open(os.path.join(folder, "summary.txt"), encoding="utf-8").read()
    assert "could not be captured: not permitted" in summary
    assert "_busy_in_the_main_thread" in summary


class _Keymap:
    def __init__(self, home):
        self.extensions_dir = str(home / "extensions")
        self.app_control = self
        self.opened = []

    def call_on_main_thread(self, callback):
        callback()

    def open_url(self, url):
        self.opened.append(url)


class _Capture:
    running = False

    def start(self, on_done):
        on_done("/data/performance/now", None)


def test_the_menu_item_opens_the_folder_when_done(tmp_path):
    keymap = _Keymap(tmp_path)
    tray.SamplingCapture(keymap, capture=_Capture()).start()
    assert keymap.opened == ["/data/performance/now"]


def test_the_menu_item_captures_beside_the_config(tmp_path):
    item = tray.SamplingCapture(_Keymap(tmp_path))
    assert item._capture._root == str(tmp_path / "performance")
    assert item._capture.seconds == 10 and not item.running
