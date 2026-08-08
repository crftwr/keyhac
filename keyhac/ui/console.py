"""The Keyhac console window (PuiKit).

Replaces keyhac-mac's SwiftUI ConsoleWindowView / keyhac-win's ckit
ConsoleWindow with one PuiKit implementation:

- log viewer (LogView) fed from keyhac.core.log.Console, colored by level
- keyboard hook on/off toggle; re-enabling reloads the config
  (keyhac-mac behavior)
- log level selector
- "Last key" / "Focus path" inspector fields with copy buttons

The console's backend owns the process's UI event loop; the keyboard hook
shares the same native loop (CGEventTap run-loop source on macOS, the
GetMessage pump on Windows).  A permanent animation-tick callback pumps new
log lines into the view and doubles as the hook health tick.
"""

import logging
import time

from puikit import Font, Panel, Style, WindowStyle
from puikit.backends import create_backend
from puikit.layout import HSplit, Item, VSplit
from puikit.widgets import Button, Checkbox, DropDown, Label, LayoutView, LogView

from keyhac.core import log

logger = log.getLogger("Console")

# Log line colors by level (matches the predecessors' scheme)
# Log text is one point below the console's 12pt UI font. Every log style
# must name the font explicitly: LogView leaves a style's font untouched once
# set, but fills a font-less style with the base-size mono face — so a line
# style without it would render at 12pt amid the 11pt stream.
_LOG_FONT = Font(size=11, monospace=True)

_LEVEL_STYLES = {
    logging.DEBUG: Style(fg=(128, 128, 128), font=_LOG_FONT),
    logging.INFO: Style(fg=(200, 200, 200), font=_LOG_FONT),
    logging.WARNING: Style(fg=(255, 255, 128), font=_LOG_FONT),
    logging.ERROR: Style(fg=(255, 128, 128), font=_LOG_FONT),
    logging.CRITICAL: Style(fg=(255, 64, 64), font=_LOG_FONT),
}
_DEFAULT_LINE_STYLE = Style(fg=(220, 220, 220), font=_LOG_FONT)

_LEVELS = [
    ("Debug", logging.DEBUG),
    ("Info", logging.INFO),
    ("Warning", logging.WARNING),
    ("Error", logging.ERROR),
]

_HEALTH_TICK_INTERVAL = 0.1

_BORDER_STYLE = Style(fg=(120, 120, 132))


class Frame(LayoutView):
    """A LayoutView that draws a clear border line around its own extent.
    draw_border() also clips the hosted content to the interior, so children
    can fill up to the line without painting over it."""

    def __init__(self, layout, margin_px: float = 6.0,
                 line_style: Style = _BORDER_STYLE):
        super().__init__(layout, margin_px=margin_px)
        self.line_style = line_style

    def draw(self, ctx) -> None:
        ctx.draw_border(self.line_style)
        super().draw(ctx)


class ConsoleWindow:
    """Owns the PuiKit backend/panel; main() runs its event loop."""

    def __init__(self, keymap, hook, settings=None):
        self._keymap = keymap
        self._hook = hook
        self._console = log.Console.get_instance()
        self._last_health_tick = time.monotonic()
        self._clipboard_provider = None
        self._clipboard_history = None
        self._last_clipboard_flush = time.monotonic()
        # App settings (keyhac.core.settings): the window's shown/hidden state
        # is restored from "console_visible" and written back whenever it
        # changes (close button hides, tray "Open Console" re-shows) — the
        # keyhac-win [CONSOLE] visible behavior. None (tests) = always show.
        self._settings = settings
        self._last_visible = None

        # start_hidden / is_main_window_visible shipped together in puikit
        # (PR #84); on an older puikit the console simply always starts
        # visible and nothing is persisted.
        from puikit.backend import Backend
        self._visibility_api = hasattr(Backend, "is_main_window_visible")
        visible = True
        if settings is not None and self._visibility_api:
            visible = bool(settings.get("console_visible", True))

        self.backend = create_backend(
            "gui",
            width=100,
            height=30,
            title="Keyhac",
            # 12pt console; the UI font shares the base font's size
            # (log lines are 11pt via _LOG_FONT)
            base_font=Font(size=12, monospace=True),
            frame_autosave_name="KeyhacConsole",
            # tool: no taskbar button / Alt-Tab entry on Windows (no-op on
            # macOS) — the tray icon is the app's sole persistent presence,
            # mirroring what activation_policy="accessory" gives macOS.
            style=WindowStyle(tool=True),
            activation_policy="accessory",       # agent app: no Dock icon (macOS)
            main_window_close="hide",            # tray-app lifecycle: close hides
            **({"start_hidden": not visible} if self._visibility_api else {}),
        )

        initial_level_index = next(
            (i for i, (_n, lvl) in enumerate(_LEVELS) if lvl == self._console.log_level), 1)

        self._hook_checkbox = Checkbox(
            "Keyboard hook", checked=hook.installed, on_change=self._on_hook_toggle)
        # Beside the hook checkbox on purpose: both switch a capability the
        # user is entitled to see the state of, and the endpoint being visibly
        # off is most of the answer to "is this thing watching me".
        #
        # The category is folded into the label rather than standing beside it
        # as its own text. A checkbox draws its box on the left and its label
        # on the right, so a separate "AI Integration:" landed between the two
        # checkboxes and read as a third peer in the row - it appeared to label
        # the box that followed it only if you already knew that was the
        # intent. A menu gets hierarchy from nesting; a flat row has to spell
        # it, and this spells the same path the menu shows.
        self._mcp_checkbox = Checkbox(
            "AI Integration: MCP server", checked=keymap.mcp_server_running,
            on_change=self._on_mcp_toggle)
        self._level_dropdown = DropDown(
            [name for name, _lvl in _LEVELS],
            selected=initial_level_index,
            on_change=self._on_level_change,
            width=12,
        )
        self._log_view = LogView(max_lines=log.Console.max_lines,
                                 style=_DEFAULT_LINE_STYLE, wrap="word")
        self._last_key_label = Label("")
        self._focus_path_label = Label("")

        self.panel = Panel(self.backend)

        # Issue #7 layout: a page margin (LayoutView inset), the log and the
        # two inspector values in bordered "content"-surface panes (Frame),
        # breathing room between the rows, inspector labels on a shared fixed
        # width so the values line up, and the toolbar/label rows centered on
        # their cross axis.
        # The flexible spacer absorbs the middle, so the only visible gaps in
        # this row are label <-> control; keep those tight.
        # The fixed spacer is not decoration: at the shared 0.3 gap "Keyboard
        # hook" and the next group read as one run of text, and the checkbox
        # that belongs to which label becomes a guess. It buys the separation
        # the grouping is claiming.
        toolbar = HSplit(
            Item(self._hook_checkbox, size="content", align="center"),
            Item(Label(""), size=3),
            Item(self._mcp_checkbox, size="content", align="center"),
            Item(Label(""), weight=1),
            Item(Label("Log level:"), size="content", align="center"),
            Item(self._level_dropdown, size="content", align="center"),
            gap=0.3,
        )
        log_pane = Frame(VSplit(Item(self._log_view, weight=1)), margin_px=6)

        def _value_field(value_label):
            return Frame(VSplit(Item(value_label, weight=1)), margin_px=4)

        # Both caption slots share one width so the value fields stay
        # X-aligned; the placeholder 12 shrinks to the widest caption as
        # actually drawn in open(), once the fonts exist to measure with.
        self._caption_items = [
            Item(Label("Last key:"), size=12, align="center"),
            Item(Label("Focus path:"), size=12, align="center"),
        ]
        inspector = VSplit(
            Item(HSplit(
                self._caption_items[0],
                Item(_value_field(self._last_key_label), weight=1,
                     hints={"surface": "content"}),
                Item(Button("Copy", on_click=self._copy_last_key, variant="secondary"),
                     size="content", align="center"),
                gap=1,
            ), size="content"),
            Item(HSplit(
                self._caption_items[1],
                Item(_value_field(self._focus_path_label), weight=1,
                     hints={"surface": "content"}),
                Item(Button("Copy", on_click=self._copy_focus_path, variant="secondary"),
                     size="content", align="center"),
                gap=1,
            ), size="content"),
            gap=0.3,
        )
        page = VSplit(
            Item(toolbar, size="content"),
            Item(log_pane, weight=1, hints={"surface": "content"}),
            Item(inspector, size="content"),
            gap=0.5,
        )
        self.panel.set_layout(VSplit(Item(LayoutView(page, margin_px=10), weight=1)))

    # ------------------------------------------------------------------
    # Lifecycle (main() drives the loop)

    def open(self) -> None:
        self.backend.open()
        # Fit the shared caption width to the widest caption as drawn (the
        # rects recompute from Item.size on every render, so mutating it here
        # is enough); measuring needs the fonts open() just created.
        width = max(self.backend.measure_text(item.content.text)
                    for item in self._caption_items)
        for item in self._caption_items:
            item.size = width
        # Backfill lines logged before the window existed, then keep pulling.
        for text, level in self._console.lines():
            self._append_line(text, level)
        self._console.pull_lines()  # already shown via lines()
        self.panel.render()
        self.backend.request_animation_ticks(self._on_pump_tick)

    def run(self) -> None:
        self.backend.run_event_loop(self._on_event)

    def close(self) -> None:
        self.backend.close()

    def quit(self) -> None:
        self.backend.quit()

    # ------------------------------------------------------------------

    def _on_event(self, event) -> None:
        self.panel.dispatch_event(event)
        self.panel.render()

    def _on_pump_tick(self) -> bool:
        """Permanent tick (PuiKit idle pump, ~10 Hz idle): pull new log lines,
        refresh the inspector fields, run the hook health check."""
        changed = False

        for text, level in self._console.pull_lines():
            self._append_line(text, level)
            changed = True

        last_key = self._console.get_text("lastKey")
        if last_key != self._last_key_label.text:
            self._last_key_label.text = last_key
            changed = True
        focus_path = self._console.get_text("focusPath")
        if focus_path != self._focus_path_label.text:
            self._focus_path_label.text = focus_path
            changed = True

        now = time.monotonic()
        if now - self._last_health_tick >= _HEALTH_TICK_INTERVAL:
            self._last_health_tick = now
            self._hook.check_health()
            if self._clipboard_provider is not None and self._clipboard_provider.poll():
                self._clipboard_history.on_clipboard_changed()
            if (self._clipboard_history is not None
                    and now - self._last_clipboard_flush >= 5.0):
                self._last_clipboard_flush = now
                self._clipboard_history.flush()
            # Persist visibility changes as they happen (polled rather than
            # event-driven: PuiKit has no visibility-change callback, and the
            # window is also hidden/shown from outside this class — the close
            # button, the tray menu). Settings.set() no-ops when unchanged.
            if self._settings is not None and self._visibility_api:
                visible = self.backend.is_main_window_visible()
                if visible != self._last_visible:
                    self._last_visible = visible
                    self._settings.set("console_visible", visible)

        if changed:
            self.panel.render()
        return True

    def attach_clipboard(self, provider, history) -> None:
        """Drive clipboard monitoring (poll) and debounced persistence from
        the console's pump tick."""
        self._clipboard_provider = provider
        self._clipboard_history = history

    def _append_line(self, text: str, level: int) -> None:
        self._log_view.append(text, style=_LEVEL_STYLES.get(level, _DEFAULT_LINE_STYLE))

    # ------------------------------------------------------------------
    # Controls

    def _on_hook_toggle(self, checked: bool) -> None:
        if checked:
            # Re-enabling the hook also reloads the config (keyhac-mac behavior)
            self._keymap.configure()
            if not self._hook.installed:
                try:
                    self._hook.install(self._keymap.on_key_event,
                                       self._keymap.on_hook_restored,
                                       self._keymap.on_mouse_event)
                except Exception as e:
                    logger.error(f"Failed to install the keyboard hook: {e}")
                    self._hook_checkbox.checked = False
        else:
            self._hook.uninstall()

    def _on_mcp_toggle(self, checked: bool) -> None:
        # Persisted before it is acted on, so a start that throws still leaves
        # the setting matching what the user asked for - and reverted with it
        # if the start fails, because a checkbox that says "on" over a socket
        # that never bound is the one state this switch must not reach.
        if self._settings is not None:
            self._settings.set("mcp_server", checked)
        try:
            if checked:
                self._keymap.start_mcp_server()
                logger.info("MCP server enabled.")
            else:
                self._keymap.stop_mcp_server()
                logger.info("MCP server disabled.")
        except Exception as e:
            logger.error(f"Could not {'start' if checked else 'stop'} "
                         f"the MCP server: {e}")
            if self._settings is not None:
                self._settings.set("mcp_server", not checked)
            self._mcp_checkbox.checked = self._keymap.mcp_server_running

    def _on_level_change(self, index: int, name: str) -> None:
        self._console.log_level = _LEVELS[index][1]
        logger.info(f"Log level: {name}")

    def _copy_last_key(self) -> None:
        self.panel.set_clipboard(self._last_key_label.text)

    def _copy_focus_path(self) -> None:
        self.panel.set_clipboard(self._focus_path_label.text)
