"""macOS window operations (the portable Window / WindowProvider API).

The macOS mirror of test_win_window.py, live against the real window server.
Unlike the Windows twin, the safe-to-mutate window lives in a *child* process
(a puikit window pumping its own run loop): AX requests into one's own app are
serviced by the very run loop pytest is blocking, so self-queries fail with
kAXErrorCannotComplete (-25208). A helper process also matches real usage -
Keyhac always manipulates *other* apps' windows.

Requires Accessibility permission for the process running pytest; skipped
otherwise (and everywhere but macOS).
"""

import os
import subprocess
import sys
import time
import threading

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS-only platform layer", allow_module_level=True)

import ApplicationServices as AS  # noqa: E402

if not AS.AXIsProcessTrusted():
    pytest.skip("Accessibility permission not granted to this process",
                allow_module_level=True)

from keyhac.platform.base import Window  # noqa: E402
from keyhac.platform.mac.window import MacWindowProvider  # noqa: E402

WINDOW_TITLE = "keyhac-window-test"

#: Opens the target window, reports readiness, then services its run loop
#: (which is what answers our AX requests) until killed.  finishLaunching()
#: matters: without it AppKit never registers the app's AX server and every
#: query from outside fails with kAXErrorCannotComplete.  The Window menu is
#: there for maximize(): the OS adds Fill and Return to Previous Size to
#: whichever menu an application registers as its windows menu.
_HELPER_SRC = f"""
from puikit.backends import create_backend
backend = create_backend("gui", width=40, height=10, title={WINDOW_TITLE!r})
backend.open()
from AppKit import NSApplication, NSMenu, NSMenuItem
app = NSApplication.sharedApplication()
main_menu = NSMenu.alloc().init()
for title in ("App", "Window"):
    item = NSMenuItem.alloc().init()
    submenu = NSMenu.alloc().initWithTitle_(title)
    item.setSubmenu_(submenu)
    main_menu.addItem_(item)
app.setMainMenu_(main_menu)
app.setWindowsMenu_(submenu)
app.finishLaunching()
print("ready", flush=True)
def handler(event):  # puikit's EventHandler is a plain callable
    pass
while True:
    backend.run_event_loop_iteration(handler, timeout_ms=100)
"""


def _wait_for(condition, timeout=5.0):
    """Poll for an out-of-process state change (window server, AX, activation).

    Pumps the run loop rather than sleeping: NSWorkspace state (frontmost
    application, running-application list) is only refreshed by run loop
    callbacks, so a plain sleep loop reads the process-start snapshot forever.
    """
    from Foundation import NSRunLoop, NSDate
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.05))
    return condition()


@pytest.fixture(scope="module")
def provider():
    return MacWindowProvider()


@pytest.fixture(scope="module")
def helper():
    """The window-owning child process."""
    process = subprocess.Popen([sys.executable, "-c", _HELPER_SRC],
                               stdout=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        assert line.strip() == "ready", "helper failed to open its window"
        yield process
    finally:
        process.kill()
        process.wait()


@pytest.fixture(scope="module")
def own_window(provider, helper):
    """The helper's window, discovered the way configs do it.

    The discovery poll must pump (see _wait_for): find_window enumerates via
    NSWorkspace, whose app list refreshes only through run loop callbacks -
    with a plain sleep the helper may never appear in this process's view.
    """
    from Foundation import NSRunLoop, NSDate
    window = None
    deadline = time.monotonic() + 5
    while window is None and time.monotonic() < deadline:
        window = provider.find_window(title=WINDOW_TITLE)
        if window is None:
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.1))
    assert window is not None, "helper window not found by find_window()"
    return window


class TestWindowIdentity:

    def test_active_window_reports_its_fields(self, provider):
        window = provider.get_active_window()
        if window is None:
            pytest.skip("no frontmost window")
        assert isinstance(window, Window)
        assert window.pid and window.pid > 0
        assert window.app_name
        assert window.class_name is None  # Win32 concept, absent on macOS
        x, y, w, h = window.get_frame()
        assert w > 0 and h > 0

    def test_own_window_fields(self, helper, own_window):
        assert own_window.title == WINDOW_TITLE
        assert own_window.pid == helper.pid
        assert not own_window.is_minimized()

    def test_own_window_uielement_surface(self, own_window):
        """The config-facing UIElement API against a live element."""
        element = own_window.native
        names = element.get_attribute_names()
        assert "AXTitle" in names and "AXPosition" in names
        assert "AXRaise" in element.get_action_names()
        # parent(): an AXWindow's parent is its AXApplication.
        parent = element.parent()
        assert parent is not None
        assert parent.get_attribute_value("AXRole") == "AXApplication"


class TestEnumeration:

    def test_own_window_is_listed(self, provider, own_window):
        assert own_window in provider.list_windows()

    def test_screen_frames_are_positive_and_primary_first(self, provider):
        frames = provider.screen_frames()
        assert frames
        assert all(w > 0 and h > 0 for _x, _y, w, h in frames)
        # CGDisplayBounds of the main display has origin (0, 0) by definition.
        assert frames[0][0] == 0.0 and frames[0][1] == 0.0

    def test_work_frames_sit_inside_the_screens(self, provider):
        """NSScreen.visibleFrame flipped into AX coordinates: one work area
        per screen, each contained in some screen frame (the menu bar/Dock
        cut can only shrink it)."""
        screens = provider.screen_frames()
        works = provider.screen_work_frames()
        assert len(works) == len(screens)
        for wx, wy, ww, wh in works:
            assert ww > 0 and wh > 0
            assert any(sx - 1 <= wx and sy - 1 <= wy
                       and wx + ww <= sx + sw + 1
                       and wy + wh <= sy + sh + 1
                       for sx, sy, sw, sh in screens)

    def test_window_frames_are_callable_from_a_worker_thread(self, provider, own_window):
        """MoveWindow's run() calls these from the thread pool.

        Resolve the lazy PyObjC Quartz symbols on the main thread first -
        their first resolution is not thread-safe - then prove the calls
        work off-main, which is the point of the CoreGraphics-not-AppKit rule.
        """
        assert provider.screen_frames() and provider.window_frames()
        result = {}
        t = threading.Thread(target=lambda: result.update(
            frames=provider.window_frames(), screens=provider.screen_frames()))
        t.start()
        t.join(timeout=15)
        assert not t.is_alive(), "window_frames() blocked in a worker thread"
        assert result["frames"] and result["screens"]


class TestFindWindow:

    def test_matches_by_title_with_wildcards(self, provider, own_window):
        assert provider.find_window(title="keyhac-window-*") is not None

    def test_conditions_are_anded(self, provider, own_window):
        assert provider.find_window(title="keyhac-window-*",
                                    app="no-such-app") is None

    def test_alternation(self, provider, own_window):
        assert provider.find_window(title="nope|keyhac-window-*") is not None

    def test_class_name_never_matches_on_mac(self, provider, own_window):
        # class_name is None on every MacWindow; a class_name condition must
        # fail rather than match vacuously.
        assert provider.find_window(class_name="*") is None

    def test_no_match_returns_none(self, provider):
        assert provider.find_window(app="no-such-application-xyz") is None


class TestFrameWrites:

    def test_set_frame_moves_without_resizing(self, own_window):
        x, y, w, h = own_window.get_frame()
        assert own_window.set_frame(x + 23, y + 17)
        assert _wait_for(
            lambda: own_window.get_frame()[:2] == (x + 23, y + 17)), \
            f"frame stayed at {own_window.get_frame()}"
        nx, ny, nw, nh = own_window.get_frame()
        assert (nw, nh) == (w, h)  # w/h omitted -> move only

    def test_set_frame_resizes_when_given_a_size(self, own_window):
        x, y, w, h = own_window.get_frame()
        assert own_window.set_frame(x, y, w + 40, h + 30)
        assert _wait_for(
            lambda: own_window.get_frame()[2:] == (w + 40, h + 30)), \
            f"frame stayed at {own_window.get_frame()}"

    def test_minimize_and_restore(self, own_window):
        assert own_window.minimize()
        assert _wait_for(own_window.is_minimized), "window did not minimize"
        assert own_window.restore()
        assert _wait_for(lambda: not own_window.is_minimized()), \
            "window did not deminiaturize"


class TestSnapLive:

    def test_snap_left_covers_the_left_half_of_the_work_area(
            self, provider, own_window, monkeypatch):
        """SnapWindow end to end: real work-area query, real AX frame write.

        Keymap.get_instance() is stubbed so no engine is needed - the point
        here is the platform half (the arithmetic is pinned in
        test_actions.py).
        """
        from keyhac.actions import MoveWindow, SnapWindow
        from keyhac.core.keymap import Keymap

        class Stub:
            def get_active_window(self):
                return own_window

            def screen_work_frames(self):
                return provider.screen_work_frames()

            def screen_frames(self):
                return provider.screen_frames()

        monkeypatch.setattr(Keymap, "get_instance", staticmethod(Stub))

        # The frame is briefly unreadable while the previous test's
        # deminiaturize animation settles.
        assert _wait_for(lambda: own_window.get_frame() is not None), \
            "window frame never became readable"

        works = provider.screen_work_frames()
        sx, sy, sw, sh = MoveWindow._get_best_screen(
            own_window.get_frame(), works)
        expected = (sx, sy, sw / 2, sh)

        SnapWindow("left")()
        assert _wait_for(
            lambda: all(abs(a - b) <= 2.0
                        for a, b in zip(own_window.get_frame(), expected))), \
            f"frame {own_window.get_frame()} never approached {expected}"


class TestActivate:

    def test_activate_brings_helper_app_frontmost(self, provider, helper, own_window):
        """activate() = AXRaise + an AXFrontmost write on the application.

        Saves whatever app was frontmost and hands focus back afterwards -
        also via AXFrontmost, because the cooperative route
        (activateWithOptions:) is ignored for non-active callers on
        macOS 14+, which is the very reason activate() works the way it does.
        """
        from AppKit import NSWorkspace
        previous = NSWorkspace.sharedWorkspace().frontmostApplication()
        try:
            assert own_window.activate()
            assert _wait_for(
                lambda: NSWorkspace.sharedWorkspace().frontmostApplication()
                        .processIdentifier() == helper.pid), \
                "helper app never became frontmost"
            active = provider.get_active_window()
            assert active is not None and active.pid == helper.pid
        finally:
            if previous is not None:
                element = AS.AXUIElementCreateApplication(
                    previous.processIdentifier())
                AS.AXUIElementSetAttributeValue(element, "AXFrontmost", True)


def _wait_settled(window, timeout=5.0):
    """Wait until the frame stops moving.

    Fill, Return to Previous Size and the way in and out of full screen
    animate for 0.3-1 s, and a frame written meanwhile is overwritten by the
    animation - each test has to start after the last one's has finished.
    """
    deadline = time.monotonic() + timeout
    last = window.get_frame()
    while time.monotonic() < deadline:
        _wait_for(lambda: False, timeout=0.2)
        frame = window.get_frame()
        if frame is not None and frame == last:
            return frame
        last = frame
    return last


def _wait_full_screen_transition():
    """Let a full-screen transition finish.

    The AX frame jumps to its final value at once, so _wait_settled cannot
    see this one, yet a request the other way made during the ~0.5 s it
    animates is silently dropped (measured: dropped at 0.4 s, honored from
    0.6 s on).
    """
    _wait_for(lambda: False, timeout=1.2)


def _close_to(frame, expected, tolerance=2.0):
    return frame is not None and all(
        abs(a - b) <= tolerance for a, b in zip(frame, expected))


class TestFillGeometry:
    """is_maximized()'s frame test, without the window server."""

    WORK = (0.0, 38.0, 1710.0, 1074.0)

    def test_the_work_area_itself_fills(self):
        from keyhac.platform.mac.window import _fills
        assert _fills(self.WORK, self.WORK)

    def test_fill_with_tiling_margins_fills(self):
        """System Settings > Desktop & Dock > Tiled windows have margins:
        Fill lands 8 px inside every edge (measured, macOS 27)."""
        from keyhac.platform.mac.window import _fills
        assert _fills((8.0, 46.0, 1694.0, 1058.0), self.WORK)

    def test_half_pixel_rounding_fills(self):
        """Menu bar auto-hidden under a notch: the work area starts at 37.5,
        and Fill rounds it."""
        from keyhac.platform.mac.window import _fills
        assert _fills((0.0, 37.0, 1710.0, 1075.0), (0.0, 37.5, 1710.0, 1074.5))

    def test_a_classic_zoom_short_of_the_work_area_does_not(self):
        """Chrome's Window > Zoom stopped 20 px short of the work area."""
        from keyhac.platform.mac.window import _fills
        assert not _fills((0.0, 38.0, 1690.0, 1054.0), self.WORK)

    def test_the_screen_the_window_is_mostly_on_is_chosen(self):
        from keyhac.platform.mac.window import _best_work_frame
        right = (1710.0, 0.0, 1920.0, 1055.0)
        assert _best_work_frame((1800.0, 10.0, 500.0, 500.0),
                                [self.WORK, right]) == right
        assert _best_work_frame((-5000.0, 0.0, 10.0, 10.0),
                                [self.WORK, right]) is None


@pytest.fixture
def frontmost_restored():
    """Hand the front back to whichever application had it: maximize() and
    restore() bring the helper forward, as Fill needs."""
    from AppKit import NSWorkspace
    previous = NSWorkspace.sharedWorkspace().frontmostApplication()
    yield
    if previous is not None:
        element = AS.AXUIElementCreateApplication(previous.processIdentifier())
        AS.AXUIElementSetAttributeValue(element, "AXFrontmost", True)


@pytest.fixture
def normal_window(own_window):
    """The helper window, out of every state and at a known small frame, and
    put back that way afterwards."""
    def settle():
        _wait_settled(own_window)
        if own_window.is_full_screen():
            _wait_full_screen_transition()
            own_window.set_full_screen(False)
            assert _wait_for(lambda: not own_window.is_full_screen()), \
                "window never left full screen"
            _wait_full_screen_transition()
        if own_window.is_minimized():
            own_window.restore()
            assert _wait_for(lambda: not own_window.is_minimized())
        assert _wait_for(lambda: own_window.get_frame() is not None)
        _wait_settled(own_window)
        own_window.set_frame(120, 140, 400, 300)
        assert _wait_for(lambda: _close_to(own_window.get_frame(),
                                           (120, 140, 400, 300))), \
            f"frame stayed at {own_window.get_frame()}"
    settle()
    yield own_window
    settle()


class TestMaximize:

    def test_maximize_fills_and_restore_returns(
            self, normal_window, frontmost_restored):
        """Window > Fill, pressed through AX, and Return to Previous Size."""
        window = normal_window
        before = window.get_frame()
        assert not window.is_maximized()
        assert window.maximize()
        assert _wait_for(window.is_maximized), \
            f"never maximized; frame {window.get_frame()}"
        assert not window.is_full_screen() and not window.is_minimized()
        assert window.restore()
        assert _wait_for(lambda: _close_to(window.get_frame(), before)), \
            f"frame {window.get_frame()} never returned to {before}"
        assert not window.is_maximized()

    def test_restore_of_a_normal_window_is_a_no_op(self, normal_window):
        assert not normal_window.restore()

    def test_without_fill_the_window_is_placed(
            self, normal_window, frontmost_restored, monkeypatch):
        """No Window > Fill to press (a macOS that renamed it, an application
        without a windows menu): the same frame, by a frame write."""
        window = normal_window
        monkeypatch.setattr(type(window), "_window_menu_item",
                            lambda self, identifier, depth: None)
        assert window.maximize()
        assert _wait_for(window.is_maximized), \
            f"never maximized; frame {window.get_frame()}"

    def test_set_frame_ends_maximized(self, normal_window, frontmost_restored):
        window = normal_window
        assert window.maximize()
        assert _wait_for(window.is_maximized), \
            f"never maximized; frame {window.get_frame()}"
        # is_maximized() turns true within tolerance of the end of Fill's
        # animation, and a frame written before it ends is overwritten.
        _wait_settled(window)
        assert window.set_frame(150, 160, 420, 310)
        assert _wait_for(lambda: _close_to(window.get_frame(),
                                           (150, 160, 420, 310)))
        assert not window.is_maximized()


class TestFullScreen:

    def test_full_screen_round_trip_is_not_maximized(
            self, normal_window, frontmost_restored):
        window = normal_window
        assert window.set_full_screen(True)
        assert _wait_for(window.is_full_screen, timeout=5), \
            "never entered full screen"
        _wait_full_screen_transition()
        # One state at a time, even where the full-screen frame equals the
        # work area (under a notch).
        assert not window.is_maximized()
        assert not window.set_frame(10, 10), \
            "a full-screen window accepted a frame write"
        assert window.restore()
        assert _wait_for(lambda: not window.is_full_screen(), timeout=5)

    def test_maximize_from_full_screen(self, normal_window, frontmost_restored):
        window = normal_window
        assert window.set_full_screen(True)
        assert _wait_for(window.is_full_screen, timeout=5)
        _wait_full_screen_transition()
        assert window.maximize()
        assert _wait_for(window.is_maximized, timeout=5), \
            f"never maximized; frame {window.get_frame()}"
        assert not window.is_full_screen()

    def test_snap_from_full_screen(self, provider, normal_window,
                                   frontmost_restored, monkeypatch):
        from keyhac.actions import MoveWindow, SnapWindow
        from keyhac.core.keymap import Keymap
        window = normal_window

        class Stub:
            def get_active_window(self):
                return window

            def screen_work_frames(self):
                return provider.screen_work_frames()

            def screen_frames(self):
                return provider.screen_frames()

        monkeypatch.setattr(Keymap, "get_instance", staticmethod(Stub))
        works = provider.screen_work_frames()
        sx, sy, sw, sh = MoveWindow._get_best_screen(window.get_frame(), works)
        expected = (sx, sy, sw / 2, sh)
        assert window.set_full_screen(True)
        assert _wait_for(window.is_full_screen, timeout=5)
        _wait_full_screen_transition()
        SnapWindow("left")()
        assert _wait_for(lambda: _close_to(window.get_frame(), expected),
                         timeout=5), \
            f"frame {window.get_frame()} never approached {expected}"
        assert not window.is_full_screen()
