"""The built-in candidate sources (discussion #112).

Each is a value the unified candidate window can be handed alongside others,
rather than an action class with a hotkey of its own - see
`keyhac.core.source` for why that distinction is the point.

The clipboard three are here as much to prove the shape as to be used: they
are the sources that already existed, so if they did not fit, the shape would
be wrong. `ShowClipboardHistory` and its siblings remain, now as one-line
presets over the same objects.

The ``Source`` suffix is not decoration: ``ClipboardHistory`` is already the
name of the history *store* on ``keymap.clipboard_history``, and two public
things with one name in a flat ``from keyhac import *`` namespace is a trap.
"""

from keyhac.core.candidate import Candidate
from keyhac.core.const import MODKEY_SHIFT
from keyhac.core.keymap import Keymap
from keyhac.core.source import CandidateSource
from keyhac.core import log

logger = log.getLogger("CandidateSource")

#: Delay between re-activating the target app and sending the paste
#: keystroke.  Only an *activating* chooser pays it - see
#: ChooserAction.activates.
_PASTE_DELAY = 0.15


class _PastingSource(CandidateSource):
    """Shared by the sources whose rows end up in the clipboard.

    Shift-Enter sets the clipboard without pasting, which is the one thing
    every clipboard row has in common.
    """

    def paste(self, text: str, modifier_flags: int) -> None:
        """Put `text` on the clipboard, and paste it unless Shift was held.

        lazydocs: ignore
        """
        from keyhac.ui import runtime

        keymap = Keymap.get_instance()
        keymap.clipboard_history.set_current(text)
        if modifier_flags & MODKEY_SHIFT:
            return
        if _chooser_took_focus():
            # The target application was deactivated to open the window and
            # has to settle before it can receive a keystroke.
            runtime.backend.call_later(_PASTE_DELAY, _send_paste)
        else:
            # It never lost the focus, so there is nothing to wait for.
            _send_paste()


def _send_paste() -> None:
    keymap = Keymap.get_instance()
    paste_key = "Cmd-V" if keymap.platform == "mac" else "Ctrl-V"
    with keymap.get_input_context() as ctx:
        ctx.send_key(paste_key)


def _chooser_took_focus() -> bool:
    from keyhac.actions import ChooserAction
    open_entry = ChooserAction._open
    return bool(open_entry and open_entry[2] is not None)


class ClipboardHistorySource(_PastingSource):
    """Everything the clipboard has held, most recent first."""

    name = "Clipboard"

    def candidates(self):
        """lazydocs: ignore"""
        history = Keymap.get_instance().clipboard_history
        return [Candidate(icon="📋", display=label, payload=text)
                for text, label in history.items()]

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        self.paste(candidate.payload, modifier_flags)


class SnippetsSource(_PastingSource):
    """Fixed text you paste often.

    ```python
    SnippetsSource([("📧", "me@example.com"), ("🕒", "Date", DateTimeSnippet("%Y-%m-%d"))])
    ```
    """

    name = "Snippet"

    def __init__(self, snippets, name: str = None):
        """Build the source.

        Args:
            snippets: Sequence of (icon, text), (icon, label, text) or
                (icon, label, callable) tuples.  A callable is invoked when
                the snippet is chosen and its return value is pasted;
                returning None pastes nothing.
            name: What the unified window shows beside these rows.
        """
        self.snippets = list(snippets)
        if name is not None:
            self.name = name

    def candidates(self):
        """lazydocs: ignore"""
        return [Candidate(icon=item[0], display=item[1],
                          payload=item[2] if len(item) > 2 else item[1])
                for item in self.snippets]

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        value = candidate.payload
        if callable(value):
            value = value()
            if value is None:
                return
        self.paste(str(value), modifier_flags)


class ClipboardToolsSource(_PastingSource):
    """Transformations applied to whatever the clipboard holds now."""

    name = "Tool"

    def __init__(self, tools, name: str = None):
        """Build the source.

        Args:
            tools: Sequence of (icon, label, callable) tuples; the callable
                takes the current clipboard text and returns the replacement.
            name: What the unified window shows beside these rows.
        """
        self.tools = list(tools)
        if name is not None:
            self.name = name

    def candidates(self):
        """lazydocs: ignore"""
        return [Candidate(icon=item[0], display=item[1], payload=item[2])
                for item in self.tools]

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        keymap = Keymap.get_instance()
        text = keymap.clipboard_history.get_current()
        if text is None:
            logger.warning("The clipboard is empty; nothing to convert.")
            return
        try:
            converted = candidate.payload(text)
        except Exception:
            logger.error(f"Clipboard tool {candidate.display!r} failed.")
            return
        if converted is None:
            return
        self.paste(str(converted), modifier_flags)
