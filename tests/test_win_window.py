"""Windows window operations (the portable Window / WindowProvider API)."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.platform.base import Window  # noqa: E402
from keyhac.platform.win.window import WinWindow, WinWindowProvider  # noqa: E402


def _dwm_extended_frame(hwnd):
    """The visible frame, read independently of the module under test."""
    import ctypes
    from ctypes import wintypes
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    rect = wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    hr = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0:
        return None
    return (float(rect.left), float(rect.top),
            float(rect.right - rect.left), float(rect.bottom - rect.top))


def _window_rect(hwnd):
    """GetWindowRect: the wider rect, invisible resize border included."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    rect = wintypes.RECT()
    assert user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (float(rect.left), float(rect.top),
            float(rect.right - rect.left), float(rect.bottom - rect.top))


@pytest.fixture(scope="module")
def provider():
    return WinWindowProvider()


@pytest.fixture(scope="module")
def own_window():
    """A real window this process owns, so frame writes are safe to test.

    Module-scoped deliberately: these tests run no message pump, so each
    opened window's teardown leaves queued messages undelivered, and creating
    one per test wedges after a handful.
    """
    puikit = pytest.importorskip("puikit.backends")
    backend = puikit.create_backend("gui", width=40, height=10, title="keyhac-window-test")
    backend.open()
    try:
        yield WinWindow(backend._hwnd)
    finally:
        backend.close()


class TestWindowIdentity:

    def test_active_window_reports_its_fields(self, provider):
        window = provider.get_active_window()
        if window is None:
            pytest.skip("no foreground window")
        assert isinstance(window, Window)
        assert window.pid and window.pid > 0
        assert window.class_name
        assert window.app_name and not window.app_name.lower().endswith(".exe")
        x, y, w, h = window.get_frame()
        assert w > 0 and h > 0

    def test_own_window_fields(self, own_window):
        assert own_window.title == "keyhac-window-test"
        assert own_window.class_name == "PuiKitWindowClass"
        assert own_window.pid > 0
        assert not own_window.is_minimized()


class TestEnumeration:

    def test_excludes_the_shell_desktop_window(self, provider):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.GetShellWindow.restype = wintypes.HWND
        shell = int(user32.GetShellWindow())
        if not shell:
            pytest.skip("no shell window")
        # Progman is owned by explorer.exe, so leaving it in makes
        # find_window(app="explorer") return the desktop.
        assert all(int(w.hwnd) != shell for w in provider.list_windows())

    def test_own_window_is_listed(self, provider, own_window):
        assert own_window in provider.list_windows()

    def test_screen_frames_are_positive_and_primary_first(self, provider):
        frames = provider.screen_frames()
        assert frames
        assert all(w > 0 and h > 0 for _x, _y, w, h in frames)
        # The primary monitor's origin is (0, 0) by definition on Windows.
        assert frames[0][0] == 0.0 and frames[0][1] == 0.0

    def test_window_frames_are_callable_from_a_worker_thread(self, provider, own_window):
        """MoveWindow's run() calls these from the thread pool.

        This is a deadlock regression test, not a smoke test: reading a window
        title is a blocking SendMessage(WM_GETTEXT) to the owning thread, and
        this test process holds an open window whose UI thread is sitting in
        pytest, not pumping. An implementation that reuses list_windows() here
        hangs forever - which is exactly what the first one did.
        """
        import threading
        result = {}
        t = threading.Thread(target=lambda: result.update(
            frames=provider.window_frames(), screens=provider.screen_frames()))
        t.start()
        t.join(timeout=15)
        assert not t.is_alive(), "window_frames() blocked in a worker thread"
        assert result["frames"] and result["screens"]


class TestFindWindow:

    def test_matches_by_class_name(self, provider, own_window):
        assert provider.find_window(class_name="PuiKitWindowClass") is not None

    def test_matches_by_title_with_wildcards(self, provider, own_window):
        assert provider.find_window(title="keyhac-window-*") is not None

    def test_conditions_are_anded(self, provider, own_window):
        assert provider.find_window(class_name="PuiKitWindowClass",
                                    title="nope-*") is None

    def test_alternation(self, provider, own_window):
        assert provider.find_window(class_name="nope|PuiKitWindowClass") is not None

    def test_no_match_returns_none(self, provider):
        assert provider.find_window(app="no-such-application-xyz") is None


class TestFrameWrites:

    def test_set_frame_moves_without_resizing(self, own_window):
        x, y, w, h = own_window.get_frame()
        assert own_window.set_frame(x + 23, y + 17)
        nx, ny, nw, nh = own_window.get_frame()
        assert (nx, ny) == (x + 23, y + 17)
        assert (nw, nh) == (w, h)  # w/h omitted -> SWP_NOSIZE

    def test_set_frame_resizes_when_given_a_size(self, own_window):
        x, y, w, h = own_window.get_frame()
        assert own_window.set_frame(x, y, w + 40, h + 30)
        _nx, _ny, nw, nh = own_window.get_frame()
        assert (nw, nh) == (w + 40, h + 30)

    def test_get_frame_is_the_frame_dwm_draws(self, own_window):
        """Not GetWindowRect, which includes the invisible resize border:
        a window positioned by that arithmetic lands short of where the OS's
        own snap puts one, and two tiles show twice the gap between them."""
        visible = _dwm_extended_frame(own_window.hwnd)
        if visible is None:
            pytest.skip("DWM reported no extended frame bounds")
        assert own_window.get_frame() == visible

    def test_set_frame_compensates_the_invisible_border(self, own_window):
        """Given a visible frame, get_frame reads the same one back, and the
        window rect underneath is the wider one (the same, on a window with
        no resize border or a DWM that will not answer)."""
        x, y, w, h = own_window.get_frame()
        assert own_window.set_frame(x + 10, y + 20, w + 40, h + 30)
        assert own_window.get_frame() == (x + 10, y + 20, w + 40, h + 30)
        wx, wy, ww, wh = _window_rect(own_window.hwnd)
        assert wx <= x + 10 and wy <= y + 20
        assert ww >= w + 40 and wh >= h + 30

    def test_minimize_and_restore(self, own_window):
        own_window.minimize()
        assert own_window.is_minimized()
        own_window.restore()
        assert not own_window.is_minimized()


@pytest.fixture
def normal_window(own_window):
    """The test window, out of every state and at a known small frame, and
    put back that way afterwards - a failed test must not leave the next one
    starting maximized."""
    def settle():
        while own_window.is_minimized() or own_window.is_maximized():
            assert own_window.restore()
        assert own_window.set_frame(120, 140, 400, 300)
        assert own_window.get_frame() == (120, 140, 400, 300)
    settle()
    yield own_window
    settle()


class TestMaximize:
    """issue #157: IsZoomed / SW_MAXIMIZE, and set_frame leaving the state."""

    def test_maximize_and_restore_round_trip(self, normal_window):
        window = normal_window
        before = window.get_frame()
        assert not window.is_maximized()
        assert window.maximize()
        assert window.is_maximized()
        assert not window.is_minimized() and not window.is_full_screen()
        assert window.get_frame() != before
        assert window.restore()
        assert not window.is_maximized()
        assert window.get_frame() == before

    def test_maximized_covers_the_work_area(self, provider, normal_window):
        """SW_MAXIMIZE pushes the resize border off the monitor; the visible
        frame get_frame reports is the work area itself."""
        window = normal_window
        assert window.maximize()
        frame = window.get_frame()
        assert any(all(abs(a - b) <= 2 for a, b in zip(frame, work))
                   for work in provider.screen_work_frames()), \
            f"maximized frame {frame} is no work area"

    def test_minimized_maximized_window_is_one_state_at_a_time(self, normal_window):
        """Minimizing a maximized window clears IsZoomed; restore() lifts
        only the minimize and brings it back maximized."""
        window = normal_window
        assert window.maximize()
        assert window.minimize()
        assert window.is_minimized()
        assert not window.is_maximized()
        assert window.restore()
        assert not window.is_minimized()
        assert window.is_maximized()
        assert window.restore()
        assert not window.is_maximized()

    def test_maximize_a_minimized_window(self, normal_window):
        window = normal_window
        assert window.minimize()
        assert window.maximize()
        assert window.is_maximized() and not window.is_minimized()

    def test_set_frame_ends_maximized(self, normal_window):
        """Otherwise the window moves but stays flagged maximized, and the
        next restore or title-bar double-click picks some other size."""
        window = normal_window
        assert window.maximize()
        assert window.set_frame(150, 160, 420, 310)
        assert not window.is_maximized()
        assert window.get_frame() == (150, 160, 420, 310)
        # Nothing left to restore: the frame written is the normal one.
        assert not window.restore()
        assert window.get_frame() == (150, 160, 420, 310)

    def test_set_frame_without_a_size_on_a_maximized_window(self, normal_window):
        """A move alone keeps the restored size, not the maximized one."""
        window = normal_window
        _x, _y, w, h = window.get_frame()
        assert window.maximize()
        assert window.set_frame(150, 160)
        assert not window.is_maximized()
        assert window.get_frame() == (150, 160, w, h)

    def test_every_verb_with_nothing_to_do_returns_false(self, normal_window):
        window = normal_window
        assert not window.restore()
        assert window.maximize()
        assert not window.maximize()
        assert window.minimize()
        assert not window.minimize()

    def test_full_screen_is_never_reported(self, normal_window):
        window = normal_window
        assert not window.enter_full_screen()
        assert not window.is_full_screen()


class TestWorkFrames:

    def test_work_frames_sit_inside_the_screens(self, provider):
        """rcWork per monitor: one work area per screen, each contained in
        its screen frame (the taskbar cut can only shrink it)."""
        screens = provider.screen_frames()
        works = provider.screen_work_frames()
        assert len(works) == len(screens)
        for wx, wy, ww, wh in works:
            assert ww > 0 and wh > 0
            assert any(sx - 1 <= wx and sy - 1 <= wy
                       and wx + ww <= sx + sw + 1
                       and wy + wh <= sy + sh + 1
                       for sx, sy, sw, sh in screens)


class TestSnapLive:

    def test_snap_left_covers_the_left_half_of_the_work_area(
            self, provider, own_window, monkeypatch):
        """SnapWindow end to end: real rcWork query, real SetWindowPos.

        Keymap.get_instance() is stubbed so no engine is needed - the point
        here is the platform half (the arithmetic is pinned in
        test_actions.py). Mirrors test_mac_window.py's TestSnapLive.
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

        works = provider.screen_work_frames()
        sx, sy, sw, sh = MoveWindow._get_best_screen(
            own_window.get_frame(), works)
        expected = (sx, sy, sw / 2, sh)

        SnapWindow("left")()
        assert all(abs(a - b) <= 2.0
                   for a, b in zip(own_window.get_frame(), expected)), \
            f"frame {own_window.get_frame()} != {expected}"

    def test_snap_restores_a_minimized_window_first(
            self, provider, own_window, monkeypatch):
        from keyhac.actions import SnapWindow
        from keyhac.core.keymap import Keymap

        class Stub:
            def get_active_window(self):
                return own_window

            def screen_work_frames(self):
                return provider.screen_work_frames()

            def screen_frames(self):
                return provider.screen_frames()

        monkeypatch.setattr(Keymap, "get_instance", staticmethod(Stub))

        own_window.minimize()
        assert own_window.is_minimized()
        SnapWindow("full")()
        assert not own_window.is_minimized()
        # A snapped-full window covers its whole work area.
        wx, wy, ww, wh = own_window.get_frame()
        assert any(abs(wx - sx) <= 2 and abs(wy - sy) <= 2
                   and abs(ww - sw) <= 2 and abs(wh - sh) <= 2
                   for sx, sy, sw, sh in provider.screen_work_frames())

    def test_snap_on_a_maximized_window_leaves_maximized(
            self, provider, normal_window, monkeypatch):
        """The #157 report: SnapWindow on maximized Chrome."""
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

        sx, sy, sw, sh = MoveWindow._get_best_screen(
            window.get_frame(), provider.screen_work_frames())
        expected = (sx, sy, sw / 2, sh)
        assert window.maximize()
        SnapWindow("left")()
        assert not window.is_maximized()
        assert all(abs(a - b) <= 2.0
                   for a, b in zip(window.get_frame(), expected)), \
            f"frame {window.get_frame()} != {expected}"
        # The snapped frame is the normal one now: maximizing and restoring
        # comes back to it, not to whatever preceded the maximize.
        snapped = window.get_frame()
        assert window.maximize()
        assert window.restore()
        assert window.get_frame() == snapped
