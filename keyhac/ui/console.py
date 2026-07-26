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

from puikit import Panel, Style, WindowStyle
from puikit.backends import create_backend
from puikit.layout import HSplit, Item, VSplit
from puikit.widgets import Button, Checkbox, DropDown, Label, LogView

from keyhac.core import log

logger = log.getLogger("Console")

# Log line colors by level (matches the predecessors' scheme)
_LEVEL_STYLES = {
    logging.DEBUG: Style(fg=(128, 128, 128)),
    logging.INFO: Style(fg=(200, 200, 200)),
    logging.WARNING: Style(fg=(255, 255, 128)),
    logging.ERROR: Style(fg=(255, 128, 128)),
    logging.CRITICAL: Style(fg=(255, 64, 64)),
}
_DEFAULT_LINE_STYLE = Style(fg=(220, 220, 220))

_LEVELS = [
    ("Debug", logging.DEBUG),
    ("Info", logging.INFO),
    ("Warning", logging.WARNING),
    ("Error", logging.ERROR),
]

_HEALTH_TICK_INTERVAL = 0.1


class ConsoleWindow:
    """Owns the PuiKit backend/panel; main() runs its event loop."""

    def __init__(self, keymap, hook):
        self._keymap = keymap
        self._hook = hook
        self._console = log.Console.get_instance()
        self._last_health_tick = time.monotonic()
        self._clipboard_provider = None
        self._clipboard_history = None
        self._last_clipboard_flush = time.monotonic()

        self.backend = create_backend(
            "gui",
            width=100,
            height=30,
            title="Keyhac",
            frame_autosave_name="KeyhacConsole",
            style=WindowStyle(),                 # a normal resizable window
            activation_policy="accessory",       # agent app: no Dock icon (macOS)
            main_window_close="hide",            # tray-app lifecycle: close hides
        )

        initial_level_index = next(
            (i for i, (_n, lvl) in enumerate(_LEVELS) if lvl == self._console.log_level), 1)

        self._hook_checkbox = Checkbox(
            "Keyboard hook", checked=hook.installed, on_change=self._on_hook_toggle)
        self._level_dropdown = DropDown(
            [name for name, _lvl in _LEVELS],
            selected=initial_level_index,
            on_change=self._on_level_change,
            width=12,
        )
        self._log_view = LogView(max_lines=log.Console.max_lines,
                                 style=_DEFAULT_LINE_STYLE)
        self._last_key_label = Label("")
        self._focus_path_label = Label("")

        self.panel = Panel(self.backend)
        self.panel.set_layout(VSplit(
            Item(HSplit(
                Item(self._hook_checkbox, size="content"),
                Item(Label(""), weight=1),
                Item(Label("Log level:"), size="content"),
                Item(self._level_dropdown, size="content"),
                gap=1,
            ), size="content"),
            Item(self._log_view, weight=1),
            Item(HSplit(
                Item(Label("Last key:"), size="content"),
                Item(self._last_key_label, weight=1),
                Item(Button("Copy", on_click=self._copy_last_key, variant="secondary"),
                     size="content"),
                gap=1,
            ), size="content"),
            Item(HSplit(
                Item(Label("Focus path:"), size="content"),
                Item(self._focus_path_label, weight=1),
                Item(Button("Copy", on_click=self._copy_focus_path, variant="secondary"),
                     size="content"),
                gap=1,
            ), size="content"),
            gap=0,
        ))

    # ------------------------------------------------------------------
    # Lifecycle (main() drives the loop)

    def open(self) -> None:
        self.backend.open()
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
                                       self._keymap.on_hook_restored)
                except Exception as e:
                    logger.error(f"Failed to install the keyboard hook: {e}")
                    self._hook_checkbox.checked = False
        else:
            self._hook.uninstall()

    def _on_level_change(self, index: int, name: str) -> None:
        self._console.log_level = _LEVELS[index][1]
        logger.info(f"Log level: {name}")

    def _copy_last_key(self) -> None:
        self.panel.set_clipboard(self._last_key_label.text)

    def _copy_focus_path(self) -> None:
        self.panel.set_clipboard(self._focus_path_label.text)
