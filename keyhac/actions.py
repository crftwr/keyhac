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

    def __repr__(self):
        return f"{type(self).__name__}()"

    def __call__(self):
        from keyhac.ui import runtime
        from keyhac.ui.chooser import ChooserWindow

        if runtime.backend is None:
            logger.error(f"{self!r} requires the UI (running with --no-ui?).")
            return

        keymap = Keymap.get_instance()
        focus = keymap.focus
        original_pid = focus.pid if focus else None

        def _refocus_original_app():
            if original_pid is not None and keymap.app_control is not None:
                keymap.app_control.activate_pid(original_pid)

        def _on_selected(item, modifier_flags):
            _refocus_original_app()
            self.on_chosen(item, modifier_flags)

        def _on_canceled():
            _refocus_original_app()

        ChooserWindow(runtime.backend, self.list_items(),
                      on_selected=_on_selected, on_canceled=_on_canceled)

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
    """Move the focused window (macOS: AX position). Simplified port of
    keyhac-mac's MoveWindow: direction + distance, clamped to the screen's
    work area; edge-snapping refinements return with the full port."""

    def __init__(self, direction: str = "", distance: float = 10):
        self.direction = direction
        self.distance = distance

    def run(self):
        from keyhac.ui import runtime
        keymap = Keymap.get_instance()
        focus = keymap.focus
        elm = focus.native if focus else None
        window = None
        while elm is not None:
            role = elm.get_attribute_value("AXRole")
            if role == "AXWindow":
                window = elm
                break
            elm = elm.get_attribute_value("AXParent")
        if window is None:
            logger.warning("MoveWindow: no focused window.")
            return
        x, y = window.get_attribute_value("AXPosition")
        w, h = window.get_attribute_value("AXSize")
        dx = {"left": -1, "right": 1}.get(self.direction, 0) * self.distance
        dy = {"up": -1, "down": 1}.get(self.direction, 0) * self.distance
        nx, ny = x + dx, y + dy
        try:
            frames = runtime.backend.screen_frames() if runtime.backend else []
            if frames:
                (_fx, _fy, fw, fh), _vis = frames[0]
                nx = max(0, min(nx, fw - w))
                ny = max(0, min(ny, fh - h))
        except Exception:
            pass
        window.set_attribute_value("AXPosition", "point", (nx, ny))

    def __repr__(self):
        return f'MoveWindow(direction="{self.direction}")'


class ActivateWindow(ThreadedAction):
    """Activate an application by name pattern (portable subset of
    keyhac-win's ActivateWindowCommand)."""

    def __init__(self, app: str):
        self.app = app

    def run(self):
        import fnmatch
        keymap = Keymap.get_instance()
        pattern = self.app.lower()
        for name, pid in keymap.app_control_running_apps():
            if any(fnmatch.fnmatch(name.lower(), p.strip())
                   for p in pattern.split("|")):
                keymap.app_control.activate_pid(pid)
                return
        logger.warning(f"ActivateWindow: no running app matches {self.app!r}")

    def __repr__(self):
        return f'ActivateWindow(app="{self.app}")'
