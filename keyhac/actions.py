"""Chooser-based actions (ported from keyhac-mac keyhac_action.py).

These live above both keyhac.core (engine) and keyhac.ui (PuiKit windows):
they are the user-facing glue between the two.
"""

import datetime

from keyhac.core.const import MODKEY_SHIFT
from keyhac.core.action import ThreadedAction
from keyhac.core.keymap import Keymap
from keyhac.core import log

logger = log.getLogger("Action")

# Delay between re-activating the target app and sending the paste keystroke,
# so the activation has settled (keyhac-win used a comparable settle wait).
_PASTE_DELAY = 0.15


class ChooserAction:
    """Base class for actions that open the chooser window.

    Derive and implement list_items() -> [(icon, label, ...)] and
    on_chosen(item, modifier_flags)."""

    #: The one chooser currently open: (action, window, original_pid).
    _open = None

    def __repr__(self):
        return f"{type(self).__name__}()"

    def __call__(self):
        from keyhac.ui import runtime
        from keyhac.ui.chooser import ChooserWindow

        if runtime.backend is None:
            logger.error(f"{self!r} requires the UI (running with --no-ui?).")
            return

        keymap = Keymap.get_instance()

        # Only one chooser at a time (issue #3): pressing the same action's
        # key again toggles its chooser closed; a different chooser action
        # replaces the open one. Either way the replacement inherits the app
        # to refocus - at this point the focus is the chooser itself, not the
        # window the user was working in.
        open_entry, ChooserAction._open = ChooserAction._open, None
        if open_entry is not None:
            prev_action, prev_chooser, original_pid = open_entry
            prev_chooser.dismiss()
            if prev_action is self:
                if original_pid is not None and keymap.app_control is not None:
                    keymap.app_control.activate_pid(original_pid)
                return
        else:
            focus = keymap.focus
            original_pid = focus.pid if focus else None

        def _refocus_original_app():
            if original_pid is not None and keymap.app_control is not None:
                keymap.app_control.activate_pid(original_pid)

        def _on_selected(item, modifier_flags):
            ChooserAction._open = None
            _refocus_original_app()
            self.on_chosen(item, modifier_flags)

        def _on_canceled():
            ChooserAction._open = None
            _refocus_original_app()

        # Center the chooser on the focused window (issue #4). Both frames are
        # portable top-left screen coordinates on both OSes; clamp to the
        # screen the window mostly lives on. UI thread here, so the window
        # accessors are allowed.
        center_on = clamp_to = None
        active = keymap.get_active_window()
        if active is not None:
            center_on = active.get_frame()
        if center_on is not None and keymap.window_provider is not None:
            clamp_to = MoveWindow._get_best_screen(
                center_on, keymap.window_provider.screen_frames())

        chooser = ChooserWindow(runtime.backend, self.list_items(),
                                on_selected=_on_selected, on_canceled=_on_canceled,
                                center_on=center_on, clamp_to=clamp_to)
        ChooserAction._open = (self, chooser, original_pid)

        # Keyhac runs as an accessory (agent) app, so the chooser must
        # deliberately activate our own process to take keyboard input; the
        # original app is re-activated on selection/cancel above. (A true
        # non-activating chooser needs an NSPanel - a planned PuiKit feature.)
        if keymap.app_control is not None:
            import os
            keymap.app_control.activate_pid(os.getpid())

    def list_items(self):
        """Virtual: list of (icon, label, ...) tuples."""
        return []

    def on_chosen(self, item, modifier_flags: int) -> None:
        """Virtual: handle the chosen item."""


class ClipboardChooserAction(ChooserAction):

    def _paste(self):
        keymap = Keymap.get_instance()
        paste_key = "Cmd-V" if keymap.platform == "mac" else "Ctrl-V"
        with keymap.get_input_context() as ctx:
            ctx.send_key(paste_key)

    def _on_chosen_common(self, text: str, modifier_flags: int):
        from keyhac.ui import runtime
        keymap = Keymap.get_instance()
        keymap.clipboard_history.set_current(text)

        # Shift-select: set the clipboard without pasting
        if modifier_flags & MODKEY_SHIFT:
            return

        # Paste once the re-activated target app has settled
        runtime.backend.call_later(_PASTE_DELAY, self._paste)


class ShowClipboardHistory(ClipboardChooserAction):
    """Show the clipboard history in the chooser window."""

    def list_items(self):
        history = Keymap.get_instance().clipboard_history
        return [("📋", label, s) for s, label in history.items()]

    def on_chosen(self, item, modifier_flags: int):
        self._on_chosen_common(item[2], modifier_flags)


class ShowClipboardSnippets(ClipboardChooserAction):
    """Show fixed snippets in the chooser window.

    snippets: sequence of (icon, label) / (icon, label, text) /
    (icon, label, callable) tuples; a callable's return value is pasted
    (None pastes nothing)."""

    def __init__(self, snippets):
        self.snippets = list(snippets)

    def list_items(self):
        return self.snippets

    def on_chosen(self, item, modifier_flags: int):
        value = item[2] if len(item) > 2 else item[1]
        if callable(value):
            value = value()
            if value is None:
                return
        self._on_chosen_common(str(value), modifier_flags)


class ShowClipboardTools(ClipboardChooserAction):
    """Show clipboard conversion tools; each tool transforms the current
    clipboard text. tools: sequence of (icon, label, func) tuples."""

    quote_mark = "> "

    def __init__(self, tools):
        self.tools = list(tools)

    def list_items(self):
        return self.tools

    def on_chosen(self, item, modifier_flags: int):
        func = item[2]
        current = Keymap.get_instance().clipboard_history.get_current() or ""
        result = func(current)
        if result is None:
            return
        self._on_chosen_common(str(result), modifier_flags)

    # -- stock converters (ported from keyhac-mac) ---------------------------

    @staticmethod
    def to_plain(s):
        return s

    @staticmethod
    def quote(s):
        return "\n".join(ShowClipboardTools.quote_mark + line
                         for line in s.splitlines())

    @staticmethod
    def unindent(s):
        import textwrap
        return textwrap.dedent(s)

    @staticmethod
    def to_half_width(s):
        return s.translate(str.maketrans(
            {chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}) | {0x3000: " "})

    @staticmethod
    def to_full_width(s):
        return s.translate(str.maketrans(
            {chr(c): chr(c + 0xFEE0) for c in range(0x21, 0x7F)}) | {0x20: "　"})


class DateTimeSnippet:
    """Callable snippet value producing the current time, e.g.
    ("🕒", "Date", DateTimeSnippet("%Y-%m-%d"))."""

    def __init__(self, fmt: str):
        self.fmt = fmt

    def __call__(self):
        return datetime.datetime.now().strftime(self.fmt)


class MoveWindow(ThreadedAction):
    """Move the focused window - full port of keyhac-mac's MoveWindow:
    direction/distance, stop at other windows' edges (window_edge) and
    screen edges (screen_edge), multi-monitor jump when already at the
    edge. x/y are deprecated (since keyhac-mac v1.64).

    Runs on both OSes: the geometry algorithm was already pure, so only the
    frame reads/writes and the screen/window queries go through the portable
    Window / WindowProvider API (keyhac.platform.base), which keeps macOS on
    AX + CoreGraphics and Windows on Win32."""

    ADJACENT_SCREEN_TOLERANCE = 50   # menu-bar gap etc.
    EDGE_TOLERANCE = 2

    def __init__(self, x: int = None, y: int = None, direction: str = "",
                 distance: float = 10, window_edge: bool = False,
                 screen_edge: bool = True):
        if x or y:
            logger.warning("MoveWindow's arguments x, y are deprecated. "
                           "Use direction and distance instead.")
            if x and x < 0:
                direction, distance = "left", abs(x)
            elif x and x > 0:
                direction, distance = "right", abs(x)
            elif y and y < 0:
                direction, distance = "up", abs(y)
            elif y and y > 0:
                direction, distance = "down", abs(y)
        self.direction = direction
        self.distance = distance
        self.window_edge = window_edge
        self.screen_edge = screen_edge
        self.wnd = None

    def starting(self):
        # ALL window reads happen here, on the main thread. On macOS these are
        # AX calls, and AX into our OWN process off the main thread SIGTRAPs
        # (they are in-process, not IPC) - and this action must also work on
        # Keyhac's own windows. Window's thread contract says the same.
        keymap = Keymap.get_instance()
        self.wnd = keymap.get_active_window()
        self.frame = self.wnd.get_frame() if self.wnd is not None else None

    @staticmethod
    def _get_best_screen(frame, screens):
        wx, wy, ww, wh = frame
        best, best_overlap = (screens[0] if screens else None), -1
        for sx, sy, sw, sh in screens:
            ox = max(0, min(wx + ww, sx + sw) - max(wx, sx))
            oy = max(0, min(wy + wh, sy + sh) - max(wy, sy))
            if ox * oy > best_overlap:
                best_overlap, best = ox * oy, (sx, sy, sw, sh)
        return best

    @classmethod
    def _find_adjacent_screen(cls, cur, screens, direction):
        cx, cy, cw, ch = cur
        tol = cls.ADJACENT_SCREEN_TOLERANCE
        best, best_overlap = None, 0
        for s in screens:
            if s == cur:
                continue
            sx, sy, sw, sh = s
            if direction == "left":
                touching = abs((sx + sw) - cx) <= tol
                overlap = min(cy + ch, sy + sh) - max(cy, sy)
            elif direction == "right":
                touching = abs(sx - (cx + cw)) <= tol
                overlap = min(cy + ch, sy + sh) - max(cy, sy)
            elif direction == "up":
                touching = abs((sy + sh) - cy) <= tol
                overlap = min(cx + cw, sx + sw) - max(cx, sx)
            else:
                touching = abs(sy - (cy + ch)) <= tol
                overlap = min(cx + cw, sx + sw) - max(cx, sx)
            if touching and overlap > best_overlap:
                best_overlap, best = overlap, s
        return best

    def run(self):
        if self.wnd is None or self.frame is None:
            logger.warning("MoveWindow: no focused window.")
            return None
        # Worker thread: pure math plus the WindowProvider's two thread-safe
        # geometry queries only - never a Window accessor (AX on macOS,
        # WM_GETTEXT-backed on Windows; both would block or crash here).
        provider = Keymap.get_instance().window_provider
        if provider is None:
            logger.warning("MoveWindow: no window provider on this platform.")
            return None
        frame = self.frame
        wx, wy, ww, wh = frame

        screens = provider.screen_frames()
        cur = self._get_best_screen(frame, screens)
        if cur is None:
            return None
        sx, sy, sw, sh = cur

        # Already at the screen edge? Jump to the adjacent monitor.
        at_edge = {
            "left": (wx - sx) <= self.EDGE_TOLERANCE,
            "right": ((sx + sw) - (wx + ww)) <= self.EDGE_TOLERANCE,
            "up": (wy - sy) <= self.ADJACENT_SCREEN_TOLERANCE,
            "down": ((sy + sh) - (wy + wh)) <= self.EDGE_TOLERANCE,
        }.get(self.direction, False)
        if at_edge:
            adj = self._find_adjacent_screen(cur, screens, self.direction)
            if adj is not None:
                ax, ay, aw, ah = adj
                if self.direction == "left":
                    wx = ax + aw - ww
                elif self.direction == "right":
                    wx = ax
                elif self.direction == "up":
                    wx = max(ax, min(wx, ax + aw - ww))
                    wy = ay + ah - wh
                elif self.direction == "down":
                    wx = max(ax, min(wx, ax + aw - ww))
                    wy = ay
                return (wx, wy)

        distance = self.distance

        # Leading edge of this window in the movement direction
        if self.direction == "left":
            front_pos, front_range, sign = wx, (wy, wy + wh), -1
        elif self.direction == "right":
            front_pos, front_range, sign = wx + ww, (wy, wy + wh), 1
        elif self.direction == "up":
            front_pos, front_range, sign = wy, (wx, wx + ww), -1
        elif self.direction == "down":
            front_pos, front_range, sign = wy + wh, (wx, wx + ww), 1
        else:
            return None

        if self.screen_edge:
            edge_dist = {
                "left": wx - sx, "right": (sx + sw) - (wx + ww),
                "up": wy - sy, "down": (sy + sh) - (wy + wh),
            }[self.direction]
            if edge_dist >= 0.1:
                distance = min(distance, edge_dist)

        if self.window_edge:
            gap = 1
            for ox, oy, ow, oh in provider.window_frames():
                if (ox, oy, ow, oh) == frame:
                    continue
                if self.direction in ("left", "right"):
                    edge_pos = (ox + ow) if self.direction == "left" else ox
                    edge_range = (oy, oy + oh)
                else:
                    edge_pos = (oy + oh) if self.direction == "up" else oy
                    edge_range = (ox, ox + ow)
                if not (front_range[1] <= edge_range[0]
                        or front_range[0] >= edge_range[1]):
                    if (edge_pos - front_pos) * sign - gap >= 0.1:
                        distance = min(distance, (edge_pos - front_pos) * sign - gap)

        if self.direction == "left":
            wx -= distance
        elif self.direction == "right":
            wx += distance
        elif self.direction == "up":
            wy -= distance
        elif self.direction == "down":
            wy += distance

        wx = max(sx, min(wx, sx + sw - ww))
        wy = max(sy, min(wy, sy + sh - wh))
        return (wx, wy)

    def finished(self, result):
        if self.wnd is None or result is None:
            return
        from keyhac.ui import runtime

        def _apply():
            self.wnd.set_frame(result[0], result[1])

        # The AX write must also run on the main thread (own-process safety);
        # call_on_main_thread is the one thread-safe puikit entry point.
        if runtime.backend is None or not runtime.backend.capabilities.supports(
                "main_thread_dispatch"):
            _apply()
        else:
            runtime.backend.call_on_main_thread(_apply)

    def __repr__(self):
        return f'MoveWindow(direction="{self.direction}")'


class SnapWindow:
    """Snap the focused window to a region of its screen (tiling).

    position: "left" | "right" | "top" | "bottom" | "full"
    ratio:    the fraction of the work area the window covers along the snap
              axis (0.5 = half the screen). Ignored for "full".

    The region is the screen's *work area* - menu bar and Dock on macOS,
    taskbar on Windows stay uncovered. "Its screen" is the one the window
    overlaps most, so repeated snaps keep a window on the monitor it is on.

    Deliberately a plain main-thread action, not a ThreadedAction: unlike
    MoveWindow there is no window-edge scan to push off-thread, just
    arithmetic - and both the Window accessors and the work-area query
    (AppKit-backed on macOS) are UI-thread only anyway.
    """

    POSITIONS = ("left", "right", "top", "bottom", "full")

    def __init__(self, position: str, ratio: float = 0.5):
        if position not in self.POSITIONS:
            raise ValueError(
                f"SnapWindow position must be one of {self.POSITIONS}, "
                f"not {position!r}")
        if not 0.1 <= ratio <= 1.0:
            raise ValueError(f"SnapWindow ratio must be in [0.1, 1.0], "
                             f"not {ratio}")
        self.position = position
        self.ratio = ratio

    def __call__(self):
        keymap = Keymap.get_instance()
        window = keymap.get_active_window()
        if window is None:
            logger.warning("SnapWindow: no focused window.")
            return
        frame = window.get_frame()
        if frame is None:
            logger.warning("SnapWindow: the focused window has no frame.")
            return
        screens = keymap.screen_work_frames() or keymap.screen_frames()
        screen = MoveWindow._get_best_screen(frame, screens)
        if screen is None:
            return
        sx, sy, sw, sh = screen
        r = self.ratio
        if self.position == "left":
            target = (sx, sy, sw * r, sh)
        elif self.position == "right":
            target = (sx + sw * (1 - r), sy, sw * r, sh)
        elif self.position == "top":
            target = (sx, sy, sw, sh * r)
        elif self.position == "bottom":
            target = (sx, sy + sh * (1 - r), sw, sh * r)
        else:
            target = (sx, sy, sw, sh)
        if window.is_minimized():
            window.restore()
        window.set_frame(*target)

    def __repr__(self):
        return f'SnapWindow("{self.position}")'


class ActivateWindow(ThreadedAction):
    """Activate a window by application name pattern (portable subset of
    keyhac-win's ActivateWindowCommand).

    Where the platform provides a WindowProvider (Windows), this matches and
    raises an actual window, so it can restore a minimized one and pick the
    front-most match. Otherwise (macOS today) it falls back to activating the
    matching *application* by pid, which is what it has always done there.

    Thread contract: all window/AppKit enumeration happens in starting() (main
    thread), run() is pure matching, and the activation is posted back to the
    main thread in finished() - Window and AX are both UI-thread only."""

    def __init__(self, app: str):
        self.app = app
        self.apps = []
        self.windows = []

    def starting(self):
        keymap = Keymap.get_instance()
        self.windows = []
        if keymap.window_provider is not None:
            # Snapshot identity here, on the main thread; run() must not touch
            # a Window (its accessors are UI-thread only).
            self.windows = [(w, w.app_name, w.title, w.class_name)
                            for w in keymap.list_windows()]
        # Running apps as a fallback: an app with no windows (or none the
        # provider can see) can still be activated by name.
        self.apps = keymap.app_control_running_apps()

    def run(self):
        import fnmatch
        pattern = self.app.lower()

        def _matches(name):
            return name is not None and any(
                fnmatch.fnmatch(name.lower(), p.strip()) for p in pattern.split("|"))

        for window, app_name, _title, _class_name in self.windows:
            # ".exe"-optional, like define_keytable(app=...)
            if _matches(app_name) or _matches((app_name or "") + ".exe"):
                return window

        for name, pid in self.apps:
            if _matches(name):
                return pid
        logger.warning(f"ActivateWindow: no window or running app matches {self.app!r}")
        return None

    def finished(self, target):
        if target is None:
            return
        from keyhac.ui import runtime
        keymap = Keymap.get_instance()

        def _apply():
            if isinstance(target, int):
                keymap.app_control.activate_pid(target)
            else:
                target.activate()

        if runtime.backend is None or not runtime.backend.capabilities.supports(
                "main_thread_dispatch"):
            _apply()
        else:
            runtime.backend.call_on_main_thread(_apply)

    def __repr__(self):
        return f'ActivateWindow(app="{self.app}")'
