"""Chooser-based actions (ported from keyhac-mac keyhac_action.py).

These live above both keyhac.core (engine) and keyhac.ui (PuiKit windows):
they are the user-facing glue between the two.
"""

import datetime

from keyhac.core import panes as panes_module
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

    Derive from it to build your own popup: implement list_items() and
    on_chosen(), and inherit the whole open / filter / refocus flow.  Only one
    chooser is open at a time - pressing the same action's key again closes
    it, and a different chooser action replaces it.

    ```python
    class PickBranch(ChooserAction):
        def list_items(self):
            return [("🌱", name) for name in git_branches()]
        def on_chosen(self, item, modifier_flags):
            checkout(item[1])
    ```
    """

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
        """Build the list the chooser shows.  Override this.

        Returns:
            A list of (icon, label) or (icon, label, ...) tuples.  Anything
            after the label is yours; on_chosen() receives the whole tuple.
        """
        return []

    def on_chosen(self, item, modifier_flags: int) -> None:
        """Handle the chosen item.  Override this.

        Args:
            item: The tuple list_items() produced for the chosen row.
            modifier_flags: Modifiers held at selection time, as a bit mask -
                the clipboard choosers read it to tell Enter from Shift-Enter.
        """


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
    """Show the clipboard history in the chooser window.

    Type to filter, Enter pastes into the application you came from,
    Shift-Enter only sets the clipboard, Escape cancels.
    """

    def list_items(self):
        """lazydocs: ignore"""
        history = Keymap.get_instance().clipboard_history
        return [("📋", label, s) for s, label in history.items()]

    def on_chosen(self, item, modifier_flags: int):
        """lazydocs: ignore"""
        self._on_chosen_common(item[2], modifier_flags)


class ShowClipboardSnippets(ClipboardChooserAction):
    """Show fixed snippets in the chooser window.

    Choosing one pastes it, exactly like the clipboard history.

    ```python
    ShowClipboardSnippets([
        ("📧", "me@example.com"),                          # (icon, text)
        ("📮", "Mailing address", "400 Broad St, ..."),    # (icon, label, text)
        ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),       # (icon, label, callable)
    ])
    ```
    """

    def __init__(self, snippets):
        """Build the action.

        Args:
            snippets: Sequence of (icon, text), (icon, label, text) or
                (icon, label, callable) tuples.  A callable is invoked when
                the snippet is chosen and its return value is pasted;
                returning None pastes nothing.
        """
        self.snippets = list(snippets)

    def list_items(self):
        """lazydocs: ignore"""
        return self.snippets

    def on_chosen(self, item, modifier_flags: int):
        """lazydocs: ignore"""
        value = item[2] if len(item) > 2 else item[1]
        if callable(value):
            value = value()
            if value is None:
                return
        self._on_chosen_common(str(value), modifier_flags)


class ShowClipboardTools(ClipboardChooserAction):
    """Show clipboard conversion tools in the chooser window.

    Each tool transforms the current clipboard text; the result is pasted like
    a history entry.  quote, unindent, to_half_width and to_full_width below
    are the stock converters, and any str -> str callable works.

    ```python
    ShowClipboardTools([
        ("🔄", "Quote", ShowClipboardTools.quote),
        ("🔄", "Upper case", str.upper),
        ("🔄", "Pretty JSON", my_pretty_json),      # str -> str
    ])
    ```

    Attributes:
        quote_mark: Prefix quote() puts on each line (default "> ").
    """

    quote_mark = "> "

    def __init__(self, tools):
        """Build the action.

        Args:
            tools: Sequence of (icon, label, func) tuples, where func takes
                the current clipboard text and returns the replacement;
                returning None leaves the clipboard alone.
        """
        self.tools = list(tools)

    def list_items(self):
        """lazydocs: ignore"""
        return self.tools

    def on_chosen(self, item, modifier_flags: int):
        """lazydocs: ignore"""
        func = item[2]
        current = Keymap.get_instance().clipboard_history.get_current() or ""
        result = func(current)
        if result is None:
            return
        self._on_chosen_common(str(result), modifier_flags)

    # -- stock converters (ported from keyhac-mac) ---------------------------

    @staticmethod
    def to_plain(s):
        """Return the text unchanged (the identity converter).

        Args:
            s: Current clipboard text.

        Returns:
            The same text.
        """
        return s

    @staticmethod
    def quote(s):
        """Prefix every line with quote_mark.

        Args:
            s: Current clipboard text.

        Returns:
            The quoted text.
        """
        return "\n".join(ShowClipboardTools.quote_mark + line
                         for line in s.splitlines())

    @staticmethod
    def unindent(s):
        """Remove the common leading whitespace from every line.

        Args:
            s: Current clipboard text.

        Returns:
            The dedented text.
        """
        import textwrap
        return textwrap.dedent(s)

    @staticmethod
    def to_half_width(s):
        """Convert full-width characters to their half-width forms.

        Args:
            s: Current clipboard text.

        Returns:
            The converted text.
        """
        return s.translate(str.maketrans(
            {chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}) | {0x3000: " "})

    @staticmethod
    def to_full_width(s):
        """Convert half-width characters to their full-width forms.

        Args:
            s: Current clipboard text.

        Returns:
            The converted text.
        """
        return s.translate(str.maketrans(
            {chr(c): chr(c + 0xFEE0) for c in range(0x21, 0x7F)}) | {0x20: "　"})


class DateTimeSnippet:
    """A ShowClipboardSnippets value that produces the current date and time.

    ```python
    ShowClipboardSnippets([("🕒", "Date", DateTimeSnippet("%Y-%m-%d"))])
    ```
    """

    def __init__(self, fmt: str):
        """Build the snippet.

        Args:
            fmt: A strftime format string, e.g. "%Y-%m-%d".
        """
        self.fmt = fmt

    def __call__(self):
        return datetime.datetime.now().strftime(self.fmt)


class MouseMove:
    """Move the mouse cursor by a relative offset.

    Held modifiers stay held, unlike the button and wheel actions.  The move
    is injected acceleration-proof, so the distance is exactly what you ask
    for (keyhac-win MouseMoveCommand).
    """

    def __init__(self, dx: int, dy: int):
        """Build the action.

        Args:
            dx: Horizontal offset in pixels, positive = right.
            dy: Vertical offset in pixels, positive = down.
        """
        self.dx = dx
        self.dy = dy

    def __call__(self):
        with Keymap.get_instance().get_input_context() as ctx:
            ctx.send_mouse_move(self.dx, self.dy)

    def __repr__(self):
        return f"MouseMove({self.dx}, {self.dy})"


class _MouseButtonAction:
    """Common shape of the button actions: validate at config-load time,
    send through the input context (which releases held modifiers around
    the button and restores them after - keyhac-win behavior)."""

    _down: bool | None = None

    def __init__(self, button: str = "left"):
        """Build the action.

        Args:
            button: "left", "right" or "middle".

        Raises:
            ValueError: Unknown button name - reported when the configuration
                loads, not when the key is pressed.
        """
        if button not in ("left", "right", "middle"):
            raise ValueError(f'{type(self).__name__} button must be "left", '
                             f'"right" or "middle", not {button!r}')
        self.button = button

    def __call__(self):
        with Keymap.get_instance().get_input_context() as ctx:
            ctx.send_mouse_button(self.button, self._down)

    def __repr__(self):
        return f'{type(self).__name__}("{self.button}")'


class MouseButtonDown(_MouseButtonAction):
    """Press a mouse button and hold it.

    Held modifiers are released first, so a modifier-bound press does not
    become a modified one (keyhac-win MouseButtonDownCommand).
    """
    _down = True


class MouseButtonUp(_MouseButtonAction):
    """Release a held mouse button (keyhac-win MouseButtonUpCommand)."""
    _down = False


class MouseButtonClick(_MouseButtonAction):
    """Click a mouse button.

    Held modifiers are released first, and rapid synthetic clicks register as
    double-clicks (keyhac-win MouseButtonClickCommand).
    """
    _down = None


class MouseWheel:
    """Turn the vertical mouse wheel (keyhac-win MouseWheelCommand)."""

    _kind = "vertical"

    def __init__(self, wheel: float):
        """Build the action.

        Args:
            wheel: Wheel notches; positive = away from you, 1.0 = one notch.
        """
        self.wheel = wheel

    def __call__(self):
        with Keymap.get_instance().get_input_context() as ctx:
            if self._kind == "vertical":
                ctx.send_mouse_wheel(self.wheel)
            else:
                ctx.send_mouse_horizontal_wheel(self.wheel)

    def __repr__(self):
        return f"{type(self).__name__}({self.wheel})"


class MouseHorizontalWheel(MouseWheel):
    """Turn the horizontal mouse wheel (keyhac-win
    MouseHorizontalWheelCommand)."""

    _kind = "horizontal"

    def __init__(self, wheel: float):
        """Build the action.

        Args:
            wheel: Wheel notches; positive = right, 1.0 = one notch.
        """
        super().__init__(wheel)


class MoveWindow(ThreadedAction):
    """Move the focused window.

    It nudges the window by `distance` pixels, or - with `window_edge` /
    `screen_edge` - travels until it meets another window's edge or the edge
    of the screen.  A window already at the screen edge hops to the adjacent
    monitor instead.

    ```python
    MoveWindow(direction="left", distance=20)
    MoveWindow(direction="left", distance=9999, window_edge=True)
    ```
    """

    # Full port of keyhac-mac's MoveWindow. Runs on both OSes: the geometry
    # algorithm was already pure, so only the frame reads/writes and the
    # screen/window queries go through the portable Window / WindowProvider
    # API (keyhac.platform.base), which keeps macOS on AX + CoreGraphics and
    # Windows on Win32.

    ADJACENT_SCREEN_TOLERANCE = 50   # menu-bar gap etc.
    EDGE_TOLERANCE = 2

    def __init__(self, x: int = None, y: int = None, direction: str = "",
                 distance: float = 10, window_edge: bool = False,
                 screen_edge: bool = True):
        """Build the action.

        Args:
            x: Deprecated since keyhac-mac v1.64; use direction and distance.
            y: Deprecated since keyhac-mac v1.64; use direction and distance.
            direction: "left", "right", "up" or "down".
            distance: How far to move, in pixels (default 10).  Pass a large
                value together with window_edge / screen_edge to travel until
                something stops it.
            window_edge: Stop at the edges of other windows (default False).
            screen_edge: Stop at the edge of the screen (default True).
        """
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
        """lazydocs: ignore"""
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
        """lazydocs: ignore"""
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
        """lazydocs: ignore"""
        # finished() is on the event-loop thread, which is where the AX write
        # has to happen (own-process safety).
        if self.wnd is None or result is None:
            return
        self.wnd.set_frame(result[0], result[1])

    def __repr__(self):
        return f'MoveWindow(direction="{self.direction}")'


class MoveFocus(ThreadedAction):
    """Move keyboard focus to the pane in a direction, within the window.

    One binding with the same meaning everywhere: `MoveFocus("left")` moves
    focus to whatever is to the left of what has it now - the next editor
    group, the tree view, the terminal panel - read off the screen rather than
    translated into whatever command that application happens to have.  Pane
    layout is something you rearrange, and a command mapping written against a
    default layout starts pointing at the wrong pane the moment you do.

    ```python
    # LEFT/RIGHT/UP/DOWN as the template defines them: Apple keyboards
    # translate Fn-Arrow into Home/End/PageUp/PageDown in hardware, so a
    # "Fn-...-Left" binding never fires (doc/configuration.md).
    table[f"{LEADER}-{MOD}-{LEFT}"]  = MoveFocus("left")
    table[f"{LEADER}-{MOD}-{RIGHT}"] = MoveFocus("right")
    ```

    **Scoped to the focused window.** At the last pane in a direction nothing
    happens; focus never leaves the window for a neighbouring one.

    **A pane that will not take focus is skipped, not stopped at.** Some panes
    accept a focus request and ignore it - Finder's sidebar and System
    Settings' detail pane both do - so the action tries each pane that way in
    turn and lands on the first that actually takes the keyboard.

    Applications with no recipe are handled by a generic rule: a big rectangle
    that holds something focusable and is not merely a container of other
    panes.  Where that picks the wrong things, `define_panes()` narrows it.
    """

    def __init__(self, direction: str, roles: str = None,
                 min_area: float = None, min_side: float = None,
                 max_depth: int = None):
        """Build the action.

        Args:
            direction: "left", "right", "up" or "down".
            roles: Role pattern that candidate panes must match, for this
                binding only.  Overrides any recipe.
            min_area: Smallest fraction of the window a pane may cover.
            min_side: Smallest a pane may be on either axis, in points.
            max_depth: Depth bound for the element walk.
        """
        if direction not in panes_module.DIRECTIONS:
            raise ValueError(
                f"MoveFocus direction must be one of "
                f"{panes_module.DIRECTIONS}, not {direction!r}")
        self.direction = direction
        self._overrides = {k: v for k, v in
                           (("roles", roles), ("min_area", min_area),
                            ("min_side", min_side), ("max_depth", max_depth))
                           if v is not None}
        self.window = None

    @classmethod
    def define_panes(cls, app: str = None, title: str = None,
                     roles: str = None, min_area: float = None,
                     min_side: float = None, max_depth: int = None) -> None:
        """Teach MoveFocus what counts as a pane in an application.

        A recipe declares *which elements are candidates*, never which key to
        send. That distinction is the point: a role set is independent of
        layout, so it survives every rearrangement - splitting an editor,
        moving the explorer to the other side, detaching a panel - that makes
        a command mapping point at the wrong pane.

        Recipes are optional. The generic rule found exactly the panes a
        person would name in every application measured; reach for this when
        it picks too much or too little in one of yours.

        ```python
        MoveFocus.define_panes(app="Code", roles="AXGroup", min_area=0.03)
        MoveFocus.define_panes(app="Finder", roles="AXScrollArea|AXOutline")
        ```

        Args:
            app: Application name pattern, matched exactly as
                `define_keytable(app=...)` matches it.  None matches any.
            title: Window title pattern.  None matches any.
            roles: Role pattern candidate panes must match.
            min_area: Smallest fraction of the window a pane may cover.
            min_side: Smallest a pane may be on either axis, in points.
            max_depth: Depth bound for the element walk.
        """
        panes_module.define_recipe(app=app, title=title, roles=roles,
                                   min_area=min_area, min_side=min_side,
                                   max_depth=max_depth)

    def starting(self):
        """lazydocs: ignore"""
        # Main thread: the window handle and the focused element's rectangle,
        # both cheap. The tree walk is NOT done here - it is dispatched from
        # run() so the action can be cancelled and so the cost is visible as
        # what it is.
        keymap = Keymap.get_instance()
        self.window = keymap.get_active_window() if keymap else None
        focus = keymap.focus if keymap else None
        element = getattr(focus, "element", None)
        self.focus_rect = None
        if element is not None:
            describe = getattr(element, "describe", None)
            if describe is not None:
                self.focus_rect = describe().get("rect")

    def run(self):
        """lazydocs: ignore"""
        from keyhac.core.wait import evaluate_on_main_thread

        if self.window is None:
            logger.warning("MoveFocus: no focused window.")
            return
        if not self.focus_rect:
            logger.warning("MoveFocus: the focused element has no rectangle, "
                           "so there is no direction to move in.")
            return
        keymap = Keymap.get_instance()
        window_node = keymap.ui.node(getattr(self.window, "element", None))
        if window_node is None:
            logger.warning("MoveFocus: the window exposes no element tree.")
            return

        settings = panes_module.settings_for(self.window)
        settings.update(self._overrides)
        # One dispatch for the whole walk: 400-odd nodes and ~35 ms on the
        # widest window measured. Per-node dispatch would be hundreds of round
        # trips, and doing it on this thread is not allowed at all.
        found = evaluate_on_main_thread(
            lambda: panes_module.find_panes(window_node, **settings))
        if len(found) < 2:
            logger.info("MoveFocus: %d pane(s) in this window; nothing to "
                        "move between.", len(found))
            return

        origin = panes_module.pane_holding(found, self.focus_rect)
        if origin is None:
            logger.info("MoveFocus: focus is not inside any pane.")
            return

        for pane in panes_module.panes_towards(found, origin.rect,
                                               self.direction):
            target = evaluate_on_main_thread(
                lambda p=pane: panes_module.focus_target(p))
            if target is None:
                continue
            if target.focus():
                logger.debug("MoveFocus: %s -> %r", self.direction, target)
                return
            # Accepted and ignored, or it went somewhere else entirely; the
            # next pane that way is a better answer than stopping here.
            logger.debug("MoveFocus: %r would not take focus, trying the "
                         "next pane %s", target, self.direction)
        logger.info("MoveFocus: nothing to the %s takes focus.", self.direction)

    def __repr__(self):
        return f'MoveFocus(direction="{self.direction}")'


class SnapWindow:
    """Snap the focused window to a region of its screen (tiling).

    The region is the screen's *work area*, so the menu bar and Dock (macOS)
    and the taskbar (Windows) stay uncovered.  "Its screen" is the one the
    window overlaps most, so repeated snaps keep a window on the monitor it is
    already on.

    ```python
    SnapWindow("left")               # left half
    SnapWindow("left", ratio=2/3)    # left two thirds
    SnapWindow("full")
    ```
    """

    # Deliberately a plain main-thread action, not a ThreadedAction: unlike
    # MoveWindow there is no window-edge scan to push off-thread, just
    # arithmetic - and both the Window accessors and the work-area query
    # (AppKit-backed on macOS) are UI-thread only anyway.

    POSITIONS = ("left", "right", "top", "bottom", "full")

    def __init__(self, position: str, ratio: float = 0.5):
        """Build the action.

        Args:
            position: "left", "right", "top", "bottom" or "full".
            ratio: Fraction of the work area the window covers along the snap
                axis, between 0.1 and 1.0 (default 0.5 = half the screen).
                Ignored for "full".

        Raises:
            ValueError: Unknown position, or a ratio outside [0.1, 1.0] -
                reported when the configuration loads, not when the key is
                pressed.
        """
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
    """Bring an application's window to the front, by name pattern.

    Where the platform enumerates windows (Windows), this raises an actual
    window, so it can restore a minimized one and pick the front-most match.
    Otherwise (macOS today) it activates the matching *application* by pid.

    ```python
    ActivateWindow(app="code|Visual Studio Code")
    ```
    """

    # A portable subset of keyhac-win's ActivateWindowCommand.
    #
    # Thread contract: all window/AppKit enumeration happens in starting()
    # (main thread), run() is pure matching, and the activation is posted back
    # to the main thread in finished() - Window and AX are both UI-thread only.

    def __init__(self, app: str):
        """Build the action.

        Args:
            app: Application name pattern, matched like define_keytable's
                app= - case-insensitive, fnmatch wildcards, "|" alternation,
                ".exe" optional.
        """
        self.app = app
        self.apps = []
        self.windows = []

    def starting(self):
        """lazydocs: ignore"""
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
        """lazydocs: ignore"""
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
        """lazydocs: ignore"""
        # finished() is on the event-loop thread, which is where activation
        # (an AX write in the own-process case) has to happen.
        if target is None:
            return
        keymap = Keymap.get_instance()
        if isinstance(target, int):
            keymap.app_control.activate_pid(target)
        else:
            target.activate()

    def __repr__(self):
        return f'ActivateWindow(app="{self.app}")'
