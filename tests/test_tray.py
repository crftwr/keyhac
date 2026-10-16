"""The tray as a status display (issues #141, #111): the busy keycap while an
action runs, a red dot for errors nobody has seen, and a menu entry for each.
Against PuiKit's MemoryBackend - the GUI backends' drawing is PuiKit's."""

import logging
import threading

from puikit import MenuItem
from puikit.backends.memory_backend import MemoryBackend

from keyhac.ui import tray
from keyhac.ui.console import UnreadErrors


class _Console:
    def __init__(self):
        self.backend = MemoryBackend(width=20, height=5)
        self.shown = 0
        self.backend.show_main_window = lambda: setattr(
            self, "shown", self.shown + 1)


def _indicator():
    console = _Console()
    items = (MenuItem("Open Console", on_select=console.backend.show_main_window),)
    return console, tray._TrayIndicator(console, items)


def _labels(menu):
    return [getattr(item, "label", "-") for item in menu.items]


def test_idle_is_the_plain_keycap_and_no_badge():
    console, indicator = _indicator()
    indicator.show((), 0)
    title, menu, tooltip, image = console.backend.tray_calls[-1]
    assert image == tray._tray_image(busy=False) and tooltip == "Keyhac"
    assert console.backend.tray_badges[-1] is None
    assert _labels(menu) == ["Open Console"]


def test_a_running_action_shows_the_busy_keycap_and_a_way_to_stop_it():
    console, indicator = _indicator()
    indicator.show(("open_issues.OpenIssues",), 0)
    _title, menu, tooltip, image = console.backend.tray_calls[-1]
    assert image == tray._tray_image(busy=True)
    assert "running open_issues.OpenIssues" in tooltip
    assert _labels(menu)[0] == "Running: open_issues.OpenIssues — Stop"


def test_unread_errors_show_a_red_dot_and_open_the_console():
    console, indicator = _indicator()
    indicator.show((), 2)
    _title, menu, tooltip, _image = console.backend.tray_calls[-1]
    assert console.backend.tray_badges[-1] == tray._ERROR_BADGE
    assert "2 unread errors" in tooltip
    entry = menu.items[0]
    assert entry.label == "2 unread errors — Open Console"
    entry.on_select()
    assert console.shown == 1


def test_an_unchanged_state_is_not_redrawn():
    """Called on every console tick; set_tray rebuilds the native menu."""
    console, indicator = _indicator()
    for _ in range(5):
        indicator.show(("a.A",), 1)
    assert len(console.backend.tray_calls) == 1


def test_stop_reaches_the_action_by_its_name():
    from keyhac.core.action import ThreadedAction

    class Probe:
        pass

    action = Probe()
    action._cancel_flag = threading.Event()
    action._run_record = type("R", (), {"name": "probe.Probe"})()
    with ThreadedAction._running_lock:
        ThreadedAction._running.add(action)
    try:
        tray._cancel("probe.Probe")
        assert action._cancel_flag.is_set()
    finally:
        with ThreadedAction._running_lock:
            ThreadedAction._running.discard(action)


def test_unread_errors_count_bursts_not_lines():
    """A traceback is many ERROR lines and one error; print() output (level
    100) is not an error; nothing counts while the console is on screen."""
    unread = UnreadErrors()
    for offset in (0.0, 0.01, 0.02):                  # one traceback
        unread.line(logging.ERROR, False, 10.0 + offset)
    unread.line(logging.ERROR, False, 20.0)           # a second error
    unread.line(100, False, 30.0)                     # print()
    unread.line(logging.WARNING, False, 40.0)
    unread.line(logging.ERROR, True, 50.0)            # seen as it happened
    assert unread.count == 2
    unread.clear()
    assert unread.count == 0


def test_a_running_action_animates_the_key_top():
    """The busy keycap alone was too close to the plain one in a single-color
    menu bar: while an action runs the key top fills and empties every
    _FRAME_SECONDS, swapping only the image - the menu is not rebuilt."""
    console, indicator = _indicator()
    period = tray._FRAME_SECONDS
    for tick in range(0, 8):                      # 10 Hz ticks over 0.8 s
        indicator.show(("a.A",), 0, now=100.0 + tick * 0.1)
    plain, busy = tray._tray_image(busy=False), tray._tray_image(busy=True)
    # set_tray showed the busy frame; t=100.0 is an even half-second (plain),
    # t=100.5 the next one (busy).
    assert console.backend.tray_images == [plain, busy]
    assert len(console.backend.tray_calls) == 1, "the menu was built once"
    # Stopped: back to the plain keycap, no more frames.
    frames = len(console.backend.tray_images)
    indicator.show((), 0, now=200.0)
    indicator.show((), 0, now=200.0 + period)
    assert console.backend.tray_calls[-1][3] == plain
    assert len(console.backend.tray_images) == frames
