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
#: query from outside fails with kAXErrorCannotComplete.
_HELPER_SRC = f"""
from puikit.backends import create_backend
backend = create_backend("gui", width=40, height=10, title={WINDOW_TITLE!r})
backend.open()
from AppKit import NSApplication
NSApplication.sharedApplication().finishLaunching()
print("ready", flush=True)
class NullHandler:
    def __getattr__(self, name):
        return lambda *a, **k: None
handler = NullHandler()
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
    """The helper's window, discovered the way configs do it."""
    window = None
    deadline = time.monotonic() + 5
    while window is None and time.monotonic() < deadline:
        window = provider.find_window(title=WINDOW_TITLE)
        if window is None:
            time.sleep(0.1)
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


class TestEnumeration:

    def test_own_window_is_listed(self, provider, own_window):
        assert own_window in provider.list_windows()

    def test_screen_frames_are_positive_and_primary_first(self, provider):
        frames = provider.screen_frames()
        assert frames
        assert all(w > 0 and h > 0 for _x, _y, w, h in frames)
        # CGDisplayBounds of the main display has origin (0, 0) by definition.
        assert frames[0][0] == 0.0 and frames[0][1] == 0.0

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
