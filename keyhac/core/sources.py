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


#: Roles a menu tree uses, in the two OSes' own vocabularies.  A menu bar's
#: shape is the same on both - a bar of top-level items, each opening a menu
#: of items, some of which open another menu - only the names differ.
_MENU_ITEM_ROLES = ("AXMenuItem", "AXMenuBarItem", "MenuItem")
_MENU_ROLES = ("AXMenu", "AXMenuBar", "Menu", "MenuBar")

#: How deep a submenu chain is followed.  Real menus are three or four deep;
#: past that something is recursive and the walk should stop rather than
#: discover it the slow way.
_MENU_MAX_DEPTH = 8


class MenuItemsSource(CandidateSource):
    """Every command in the front application's menus, as one flat list.

    This is the long tail the candidate window is for: the commands that have
    no keyboard shortcut, in an application whose menus you do not know by
    heart.  Rows read as the path to them - `File › Export › As PDF…` - and
    carry the shortcut where there is one, so choosing from here twice teaches
    the key the third time.

    Only leaves are offered.  A row that merely opens another menu is not a
    command, and a list of them would be a worse menu bar rather than a
    better one.  Disabled items are skipped: they are visible in the menu for
    the shape of it, and unchoosable here.

    **It costs a real traversal.**  Measured on macOS: 79 ms for a small
    application, 396 ms for Chrome, for 161 and 331 items - so this belongs
    in a `Scope` of its own, where it is paid for when asked for, rather than
    in a merged scope opened on every keystroke.
    """

    name = "Menu"

    def __init__(self, name: str = None):
        """Build the source.

        Args:
            name: What a shared window shows beside these rows.
        """
        if name is not None:
            self.name = name

    def candidates(self):
        """lazydocs: ignore"""
        element = self._front_element()
        if element is None:
            logger.debug("No front window; there is no menu bar to read.")
            return []
        try:
            bar = element.menu_bar()
        except Exception:
            logger.debug("This platform does not expose a menu bar.")
            return []
        if bar is None:
            logger.debug("The front application exposes no menu bar.")
            return []
        rows = []
        _walk_menu(bar, (), rows, 0)
        if not rows:
            logger.debug("The front application's menu bar walked to nothing.")
        return rows

    @staticmethod
    def _front_element():
        """An element inside the *front application*, to find its menu bar
        from.

        The active window, deliberately, and not `keymap.focus`. A `Focus`
        mixes its sources - its window title comes from the AX-focused
        application, which the popup itself can become - and reading the menu
        bar from that gets Keyhac's, which as an accessory app has none. The
        symptom is an empty list. `get_active_window()` reads the frontmost
        application throughout, and nothing about the popup moves it.
        """
        keymap = Keymap.get_instance()
        if keymap is None:
            return None
        try:
            window = keymap.get_active_window()
        except Exception:
            return None
        return getattr(window, "native", None) if window is not None else None

    def badge(self, candidate) -> str:
        """lazydocs: ignore"""
        return candidate.extras.get("shortcut", "")

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        element = candidate.payload
        try:
            if not element.perform_action("AXPress") and \
                    not element.perform_action("Invoke"):
                logger.error(f"{candidate.display}: the menu item refused to "
                             f"be pressed.")
        except Exception:
            logger.error(f"{candidate.display}: pressing it failed.")


def _walk_menu(node, path, rows, depth) -> None:
    """Flatten a menu tree onto `rows`, keeping the path to each leaf."""
    if depth > _MENU_MAX_DEPTH:
        return
    try:
        children = node.children()
    except Exception:
        return
    for child in children or []:
        try:
            role = child.describe().get("role")
        except Exception:
            continue
        if role in _MENU_ROLES:
            _walk_menu(child, path, rows, depth + 1)
            continue
        if role not in _MENU_ITEM_ROLES:
            continue
        title = _menu_title(child)
        here = path + (title,) if title else path
        submenus = [c for c in (child.children() or [])
                    if _role_of(c) in _MENU_ROLES]
        if submenus:
            for submenu in submenus:
                _walk_menu(submenu, here, rows, depth + 1)
            continue
        if not title or not _menu_enabled(child):
            continue
        rows.append(Candidate(
            icon="≡", display=" › ".join(here), payload=child,
            extras={"shortcut": _menu_shortcut(child)}))


def _role_of(element):
    try:
        return element.describe().get("role")
    except Exception:
        return None


def _menu_title(element) -> str:
    try:
        return element.describe().get("name") or ""
    except Exception:
        return ""


def _menu_enabled(element) -> bool:
    """A disabled item is unchoosable, so it is not offered.  Unknown counts
    as enabled: a platform that does not say must not silently empty the
    list."""
    for attribute in ("AXEnabled", "IsEnabled"):
        try:
            value = element.get_attribute_value(attribute)
        except Exception:
            continue
        if value is not None:
            return bool(value)
    return True


def _menu_shortcut(element) -> str:
    """The item's keyboard shortcut, spelled the way a key table would.

    The modifier mask was read off real menus rather than a header, because
    two of its bits are not what one would guess:

    ====  ==============================================
    0x01  Shift
    0x02  Option / Alt
    0x04  Control
    0x08  **clears** the otherwise implicit Command
    0x10  Fn
    ====  ==============================================

    So a plain `Cmd-D` reports 0, and `Ctrl-Tab` - which has no Command -
    reports 0x08 | 0x04.  The evidence: Terminal's Split Pane (Cmd-D) is 0,
    Close Split Pane (Cmd-Shift-D) is 1, Show Next Tab (Ctrl-Tab) is 12, and
    Fill (Fn-Ctrl-F) is 28.

    A key with no character - Home, Page Up, Tab - reports a private-use
    glyph that would print as a box, but also a virtual key code, and that
    goes through Keyhac's own name table.  The shortcut then reads in exactly
    the spelling a config would write it in.
    """
    try:
        char = element.get_attribute_value("AXMenuItemCmdChar") or ""
        virtual_key = element.get_attribute_value("AXMenuItemCmdVirtualKey")
        modifiers = element.get_attribute_value("AXMenuItemCmdModifiers")
    except Exception:
        return ""
    key = ""
    if virtual_key is not None:
        try:
            from keyhac.core.vk import get_key_names
            key = get_key_names().vk_to_str(int(virtual_key))
        except Exception:
            key = ""
        if key.startswith("("):        # vk_to_str's "unknown" spelling
            key = ""
    if not key:
        if not char or not str(char).isprintable():
            return ""
        key = str(char)
    mask = int(modifiers or 0)
    parts = []
    if not mask & 0x08:
        parts.append("Cmd")
    if mask & 0x10:
        parts.append("Fn")
    if mask & 0x04:
        parts.append("Ctrl")
    if mask & 0x02:
        parts.append("Alt")
    if mask & 0x01:
        parts.append("Shift")
    return "-".join(parts + [key])


#: How deep a chain of multi-stroke prefixes is followed.  Two or three is
#: already unusual; past this something is recursive.
_PREFIX_MAX_DEPTH = 6


class KeyBindingsSource(CandidateSource):
    """Every key binding in effect right now, and a way to run one.

    The one source nothing outside Keyhac can offer: it is the engine's own
    tables, resolved the way the hook resolves them - the tables whose focus
    condition matches where the user is standing, merged in definition order,
    or the armed multi-stroke table when there is one.  Re-deriving that from
    the configuration would be a second implementation of the rule, and the
    two would drift.

    It is also the cheap one.  There is no traversal and no other process to
    ask; the answer is a dict the engine already keeps up to date.

    A multi-stroke prefix is **expanded to its leaves**, the way the menu
    source expands submenus - `Fn-X › A` is the sequence you would type, and
    those are exactly the bindings nobody remembers.  Rows show what the
    binding does, with the keys themselves right-aligned, so the list reads
    as a reference: what can I press here, and what would it do.

    Choosing a row **runs it**, which is the point rather than a bonus - a
    binding you can run from a list is one that does not need a key of its
    own, and running out of keys is what the candidate window exists to fix.
    """

    name = "Key"

    def __init__(self, name: str = None):
        """Build the source.

        Args:
            name: What a shared window shows beside these rows.
        """
        if name is not None:
            self.name = name

    def candidates(self):
        """lazydocs: ignore"""
        keymap = Keymap.get_instance()
        if keymap is None:
            return []
        rows = []
        _walk_bindings(keymap.effective_keytable(), (), rows, 0)
        return rows

    def badge(self, candidate) -> str:
        """lazydocs: ignore"""
        return candidate.extras.get("keys", "")

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        keymap = Keymap.get_instance()
        action = candidate.payload
        try:
            if callable(action):
                action()
                return
            items = action if isinstance(action, (list, tuple)) else [action]
            with keymap.get_input_context() as ctx:
                for item in items:
                    ctx.send_key(item)
        except Exception:
            logger.error(f"{candidate.display}: running the binding failed.")


def _walk_bindings(table, path, rows, depth) -> None:
    """Flatten a key table onto `rows`, expanding multi-stroke prefixes."""
    from keyhac.core.key import KeyTable
    from keyhac.core.keymap import _key_text

    if table is None or depth > _PREFIX_MAX_DEPTH:
        return
    for key, action in table.items():
        keys = path + (_key_text(key),)
        if isinstance(action, KeyTable):
            _walk_bindings(action.table, keys, rows, depth + 1)
            continue
        rows.append(Candidate(
            icon="⌨", display=_action_text(action), payload=action,
            match_text=f"{' '.join(keys)} {_action_text(action)}",
            extras={"keys": " › ".join(keys)}))


def _action_text(action) -> str:
    """What a binding does, in one line.

    An instance falls back to its class name when it has no `__repr__` of its
    own: the default one is `<module.Class object at 0x...>`, which in a list
    of things you might run reads as a failure rather than as a name.  The
    built-in actions all define one; an operator's class often will not.
    """
    if isinstance(action, str):
        return action
    if isinstance(action, (list, tuple)):
        return " ".join(str(item) for item in action)
    name = getattr(action, "__name__", None)
    if name:
        return f"{name}()"
    text = repr(action)
    if text.startswith("<") and " object at 0x" in text:
        return f"{type(action).__name__}()"
    return text
