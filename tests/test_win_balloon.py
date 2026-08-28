"""BalloonManager live on Windows - one puikit screen mark per balloon.

Mirrors the macOS live verification: the balloon exists as a mark, sits in the
main screen's work area, does not take the foreground, and closes cleanly.
What a mark *is* on Windows (a layered, click-through window painted by
UpdateLayeredWindow) is puikit's own test; this one covers the part keyhac
owns - naming, replacement and placement.
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from keyhac.ui.balloon import BalloonManager  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND


@pytest.fixture(scope="module")
def backend():
    puikit = pytest.importorskip("puikit.backends")
    backend = puikit.create_backend(
        "gui", width=40, height=10, title="keyhac-balloon-test")
    backend.open()
    yield backend
    backend.close()


def test_balloon_pops_and_closes(backend):
    manager = BalloonManager(backend)
    fg_before = user32.GetForegroundWindow()

    manager.pop("test", "multi-stroke: LEADER-")
    marker = manager._balloons["test"]
    assert not marker.closed

    # A balloon is a note about what the user is doing, so popping one must
    # not move the foreground away from what they are doing it in.
    assert user32.GetForegroundWindow() == fg_before

    # Placement: inside the main screen's work area (top-right corner).
    x, y, w, h = marker._rect
    (_full, (vx, vy, vw, vh)) = backend.screen_frames()[0]
    assert vx <= x and x + w <= vx + vw
    assert vy <= y and y + h <= vy + vh

    manager.close("test")
    assert not manager._balloons
    assert marker.closed


def test_balloon_replaces_same_name(backend):
    manager = BalloonManager(backend)
    manager.pop("name", "first")
    first = manager._balloons["name"]
    manager.pop("name", "second, a fair bit longer than the first")
    second = manager._balloons["name"]
    assert first is not second
    assert first.closed
    assert not second.closed
    manager.close()
    assert not manager._balloons
    assert second.closed


def test_a_long_balloon_wraps_instead_of_being_cut_short(backend):
    """The width the old window could not exceed is a wrap width now: a long
    balloon grows down rather than losing its tail."""
    manager = BalloonManager(backend)
    manager.pop("short", "brief")
    short_h = manager._balloons["short"]._rect[3]
    manager.pop("long", "A balloon with a great deal more to say than the "
                        "one above it, which has to wrap onto several lines.")
    long_marker = manager._balloons["long"]
    assert long_marker._rect[3] > short_h
    assert len(long_marker._spec["lines"]) > 1
    manager.close()
