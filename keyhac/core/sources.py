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

import time
import traceback

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
            # With the reason omitted, a tool that raises is indistinguishable
            # from one that does nothing - and the tools are user code.
            logger.error(f"Clipboard tool {candidate.display!r} failed:\n"
                         f"{traceback.format_exc()}")
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
    in a `ChooserPage` of its own, where it is paid for when asked for, rather than
    in a merged page opened on every keystroke.

    **macOS only.**  There the menu bar is an OS-level part: one per
    application, always at the top of the screen, and readable in full while
    it is closed.  A Windows menu belongs to a window, may not be there at
    all, and is populated only when it opens - so this source finds no menu
    bar there and yields nothing.  Nothing is lost: a menu's top-level items
    are UI elements of the window like any other, and `WindowControlsSource`
    lists them with everything else clickable.
    """

    name = "Menu"

    #: The walk reads *another* application's menu bar and touches nothing of
    #: Keyhac's, so it belongs on a worker.  What that is worth: this source
    #: is a few hundred round trips into the other process, and drained on
    #: the main thread at 2 ms per 10 Hz tick it took seconds to reach the
    #: screen.  `candidates()` below resolves the menu bar before it returns
    #: the generator, so the part that needs the main thread happens there.
    background = True

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
        return _walk_menu(bar, (), 0)

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

        `window.element`, not `window.native`: `native` is *the platform's own
        object*, which is an AX element on macOS but the HWND wrapper on
        Windows, and an HWND wrapper has no menu bar to find. `element` is the
        documented bridge from a window to element introspection and answers
        an element on both (`Window.element`).
        """
        keymap = Keymap.get_instance()
        if keymap is None:
            return None
        try:
            window = keymap.get_active_window()
        except Exception:
            return None
        return getattr(window, "element", None) if window is not None else None

    def badge(self, candidate) -> str:
        """lazydocs: ignore"""
        return candidate.extras.get("shortcut", "")

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        if not _press(candidate.payload):
            logger.error(f"{candidate.display}: the menu item refused to "
                         f"be pressed.")


#: How far up from the element the screen reports at a point to look for the
#: one being pressed.  The point tested is the middle of the control, so what
#: is at it is usually the control's own label rather than the control - two
#: levels in VS Code, a text node inside a group inside the tab.  Ten leaves
#: room for a deeper skin without letting a miss walk all the way up to the
#: window.
_HIT_TEST_DEPTH = 10


def _click_control(element, what: str = "") -> bool:
    """Click `element` where it is on the screen, if the screen agrees that
    is where it is.  False means it could not be proven, not that it failed.

    **An accessibility press is accepted by applications that then do
    nothing.** Measured in VS Code on macOS, 8/8 either way: with
    `AXEnhancedUserInterface` unset, AXPress on the panel's Problems tab and
    on the activity bar's Search returns success and moves nothing; with it
    set, the same presses switch the panel and the sidebar. It is Chromium's
    *rendered content* that is conditional, not Electron: Chrome's own toolbar
    is Views in the browser process and answers a press unconditionally, while
    a button in the page it is showing does not - which is why a chooser row
    can look like it works in Chrome and not in VS Code, whose entire UI is
    the page. Unset is the normal state, since Keyhac asks for
    that flag only through `keymap.ui.enable_content_access()`, which is the
    AI integration's to call. A chooser must not call it: it also puts VS
    Code into its screen-reader-optimised rendering, which is why enabling it
    is an explicit call and never something a walk does on its own
    (`platform/mac/uielement.py`). The row said "press this", the press said
    yes, and the panel did not move - a silent wrong thing with no error to
    report, which is why the press is no longer the first thing tried.

    A real click is what the row meant anyway: this source lists what you
    could click, and a click is the one thing no widget can accept and
    ignore.

    **What makes it safe is the hit test.** The OS is asked what is at the
    point about to be clicked, and the click only goes out when the answer is
    this element or something inside it. That rules out the stale rectangle,
    the window that moved, the control scrolled out of sight and the popover
    on top - each of which would otherwise click whatever is there instead,
    which is worse than not clicking at all. Anything that cannot be proven
    falls through to `_press`, which is where an off-screen control, and a
    platform that cannot answer the question, still belong.

    The pointer goes back where the user left it: parked on the control it
    would raise a tooltip and change what their next click means.
    """
    keymap = Keymap.get_instance()
    if keymap is None:
        return False
    rect = _rect_of(element)
    if rect is None or rect[2] <= 0 or rect[3] <= 0:
        return False
    point = _centre(rect)
    at = _is_at(element, point)
    if at is False:
        # The one refusal the application itself can fix.
        moved = _scrolled_into_view(element, rect)
        if moved is not None and moved[2] > 0 and moved[3] > 0:
            rect, point = moved, _centre(moved)
            at = _is_at(element, point)
    if at is not True:
        if at is False:
            _report_unreachable(what or "the control")
        else:
            # The platform cannot answer "what is at this point?" - Windows
            # does not verify its ElementFromPoint yet - so there is nothing
            # to report: this is the press path, as it always was.
            logger.debug("This platform cannot say what is at a point.")
        return False
    origin = keymap.cursor_pos()
    if origin is None:
        return False
    try:
        with keymap.get_input_context() as ctx:
            ctx.send_mouse_move(point[0] - origin[0], point[1] - origin[1])
            ctx.send_mouse_button("left")
            ctx.send_mouse_move(origin[0] - point[0], origin[1] - point[1])
    except Exception:
        logger.debug("The control could not be clicked.", exc_info=True)
        return False
    logger.debug(f"Clicked the control at {point}.")
    return True


#: What "bring this into view" is called.  macOS only: the UIA counterpart is
#: the ScrollItem pattern, which the Windows element does not wire yet - and
#: the name is filtered against what the element offers, so nothing there logs
#: an unknown-action warning for it.
_SCROLL_ACTIONS = ("AXScrollToVisible",)

#: How long to let a scroll land before giving up on it, and how often to look.
#: Bounded hard because this runs on the event-loop thread: the chooser answers
#: a selection immediately, which is the whole reason it cannot wait for
#: content access (~2 s) either.  A scroll that has not moved the rectangle in
#: 150 ms is one that is not going to.
_SCROLL_SETTLE = 0.15
_SCROLL_POLL = 0.03


def _scrolled_into_view(element, rect) -> tuple | None:
    """Ask for the element to be brought into view; its new rectangle, or None.

    The hit test refuses to click what is not at the point, which is right,
    but "scrolled out of sight" is the one refusal the row's own application
    can fix. A control the user picked by name is one they want acted on, and
    scrolling to reveal it is what a person would do before clicking it.

    Only on the way to a click, never while the selection is merely moving
    through the list: a list that scrolls the document you are reading it
    against changes what it is describing, which is what `ChooserWindow`
    already refuses by not confirming on a row click.
    """
    try:
        available = set(element.get_action_names() or ())
    except Exception:
        return None
    scrolled = False
    for action in _SCROLL_ACTIONS:
        if action not in available:
            continue
        try:
            scrolled = bool(element.perform_action(action))
        except Exception:
            scrolled = False
        if scrolled:
            break
    if not scrolled:
        return None
    deadline = time.monotonic() + _SCROLL_SETTLE
    while True:
        moved = _rect_of(element)
        if moved is not None and moved != rect:
            return moved
        if time.monotonic() >= deadline:
            return None
        time.sleep(_SCROLL_POLL)


def _report_unreachable(what: str) -> None:
    """Say that the row could not be clicked - on screen, not only in the log.

    The console is not up during a keyboard flow, so a `logger.error` alone is
    what "nothing happened and nothing was reported" looked like before. The
    balloon is the established way to answer a keystroke (`ReportCaretAnchor`
    does the same), and it goes over the window rather than at the control:
    the control not being where it said it was *is* the failure, so there is
    nothing to point at.
    """
    logger.error(
        f"{what}: could not be clicked - it is not at its rectangle (covered "
        f"by another window, or scrolled out of view). Pressed it instead, "
        f"which an application that draws its own controls may accept and do "
        f"nothing with.")
    keymap = Keymap.get_instance()
    pop = getattr(keymap, "pop_balloon", None) if keymap is not None else None
    if pop is None:
        return
    where = {}
    try:
        from keyhac.ui.balloon import _focused_window_rect
        rect = _focused_window_rect(keymap)
        if rect is not None:
            where = {"over": rect}
    except Exception:
        # Placement is the nicety; the message is the point.
        pass
    try:
        pop("Chooser", f"{what}: could not click it - it is not where it said "
                       f"it was (covered, or scrolled away).", 4.0, **where)
    except Exception:
        logger.debug("No balloon to report the unreachable control with.")


def _rect_of(element) -> tuple | None:
    """Where the element is *now* - not where the walk recorded it.

    The rows stream and the window stays up while the user types, so the
    candidate's rectangle can be seconds old and the view under it can have
    scrolled since.  The point about to be clicked has to come from the
    element as it is.
    """
    describe = getattr(element, "describe", None)
    if describe is None:
        return None
    try:
        rect = describe().get("rect")
    except Exception:
        return None
    if not (isinstance(rect, (tuple, list)) and len(rect) == 4):
        return None
    return tuple(rect)


def _centre(rect) -> tuple:
    return (int(rect[0] + rect[2] / 2), int(rect[1] + rect[3] / 2))


def _is_at(element, point):
    """Whether `point` lands on `element` rather than on something over it.

    Three answers, because two of them mean different things to the user:
    True, False (something else is there - worth saying out loud), and **None
    for "could not ask"**, which is a platform that has no hit test rather
    than a control that cannot be reached. Reporting the second as the first
    would put a warning in front of every Windows press.

    The element under the point is usually a descendant - the label inside the
    button - so the answer is yes for the element itself and for anything it
    contains, and the walk up stops at `_HIT_TEST_DEPTH`.
    """
    at_point = getattr(type(element), "element_at_point", None)
    if at_point is None:
        return None
    try:
        found = at_point(point[0], point[1])
    except Exception:
        return None
    if found is None:
        return None
    for _ in range(_HIT_TEST_DEPTH):
        if found is None:
            return False
        if _same_element(found, element):
            return True
        try:
            found = found.parent()
        except Exception:
            return False
    return False


def _same_element(one, other) -> bool:
    """Whether two references point at the same element.

    By identity where the platform has one - macOS AX refs compare by
    CFEqual.  Windows answers None there (its control view is a real tree, so
    nothing pays for a runtime id) and `None == None` would make every
    element the same one, so the description stands in: same place, same
    role, same name is the same control for the purpose of aiming a click at
    it.
    """
    key, other_key = _identity_of(one), _identity_of(other)
    if key is not None and other_key is not None:
        return bool(key == other_key)
    try:
        mine, theirs = one.describe(), other.describe()
    except Exception:
        return False
    return all(mine.get(field) == theirs.get(field)
               for field in ("role", "name", "rect"))


#: What "press this" is called, in the order to try it - the AX names and the
#: UIA ones in one list, since a row can come from either platform.
#:
#: macOS says it in one word: AXPress is how a button, a tab, a checkbox and a
#: disclosure triangle are all activated. UIA gives each its own *pattern*, so
#: the same idea is four names here, and the order is what a click would do:
#: run it (Invoke), else select it (a tab, a tree row), else flip it (a toggle
#: button), else open it (a menu item, an expandable header). Measured in VS
#: Code, which has all of them: 279 rows offer only Invoke, 19 only Select, 26
#: only Expand/Collapse and 5 only Toggle - so a list with Invoke alone
#: refused every tab in the activity bar, which is what
#: "Extensions (Ctrl+Shift+X)" was.
_PRESS_ACTIONS = ("AXPress", "Invoke", "Select", "Toggle", "Expand", "AXOpen")


def _press(element) -> bool:
    """Press `element`, whatever this platform calls that.

    It asks the element which actions it *has* before trying any, because a
    name from the other platform is not a miss to try past - it is a name this
    platform has never heard of, and Windows logs a warning for each one.
    Probing blind therefore put "Unknown UI Automation action: 'AXPress'" in
    the console on every press there. An element that cannot say (a platform
    without the query) is probed in order, as before.

    **Focus is the last resort**, for a row whose platform offers no press
    pattern at all - a text field, and Chromium's list items (26 of them in VS
    Code). Clicking a text field is how the caret gets into it, so focusing
    one *is* pressing it; for the rest it is the closest honest thing, and it
    beats telling the user the row they chose does nothing.
    """
    try:
        available = set(element.get_action_names() or ())
    except Exception:
        available = None
    for action in _PRESS_ACTIONS:
        if available is not None and action not in available:
            continue
        try:
            if element.perform_action(action):
                return True
        except Exception:
            continue
    try:
        if element.set_focus():
            logger.debug("Nothing pressed it; the focus was put on it instead.")
            return True
    except Exception:
        pass
    return False


#: What one read of a menu leaf asks for: whether it can be chosen, and the
#: three parts of its keyboard shortcut.  Both platforms' spellings of
#: "enabled" sit in the same list on purpose - an attribute the element does
#: not have costs nothing extra in a batched read, and asking for both keeps
#: this one call rather than one call and a fallback.
_MENU_ITEM_ATTRS = ("AXEnabled", "IsEnabled", "AXMenuItemCmdChar",
                    "AXMenuItemCmdVirtualKey", "AXMenuItemCmdModifiers")


def _read(element, names):
    """The named attributes of one element, in as few round trips as the
    platform allows.

    An element that can answer them together does; anything else - a fake in
    a test, an element from a platform layer written before this existed - is
    asked one at a time and gives back the same dict.
    """
    batched = getattr(element, "get_attribute_values", None)
    if batched is not None:
        try:
            return batched(list(names))
        except Exception:
            return {}
    got = {}
    for name in names:
        try:
            got[name] = element.get_attribute_value(name)
        except Exception:
            got[name] = None
    return got


def _walk_menu(node, path, depth):
    """Yield a menu tree's leaves, keeping the path to each.

    A generator rather than a list: the window shows the File menu while Help
    is still being read, and each `yield` is a place the walk can be
    abandoned when the window closes under it.

    **Round trips are the cost, not the data.**  Every read is answered by the
    other application on its own main thread, so what a walk costs is how many
    times it asks.  A leaf is three questions now - what it is, whether it
    opens a submenu, and the batched rest - where it was seven, and two of the
    seven asked again for what `describe()` had already answered.  Measured
    over three applications' menu bars: 4-5x fewer calls, 3x less wall clock,
    the same rows.
    """
    if depth > _MENU_MAX_DEPTH:
        return
    try:
        children = node.children()
    except Exception:
        return
    for child in children or []:
        try:
            described = child.describe()
        except Exception:
            continue
        role = described.get("role")
        if role in _MENU_ROLES:
            yield from _walk_menu(child, path, depth + 1)
            continue
        if role not in _MENU_ITEM_ROLES:
            continue
        # describe() answers the name along with the role; asking for it
        # again is a second round trip for something already in hand.
        title = described.get("name") or ""
        here = path + (title,) if title else path
        submenus = [c for c in (child.children() or [])
                    if _role_of(c) in _MENU_ROLES]
        if submenus:
            for submenu in submenus:
                yield from _walk_menu(submenu, here, depth + 1)
            continue
        if not title:
            continue
        # Only a leaf is ever offered, so only a leaf pays for this read.
        values = _read(child, _MENU_ITEM_ATTRS)
        if not _enabled_from(values):
            continue
        yield Candidate(
            icon="≡", display=" › ".join(here), payload=child,
            extras={"shortcut": _shortcut_from(values)})


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
    return _enabled_from(_read(element, ("AXEnabled", "IsEnabled")))


def _enabled_from(values) -> bool:
    """The same answer, read off attributes already in hand - which is how the
    walk asks, since it has them from the one read it makes per leaf."""
    for attribute in ("AXEnabled", "IsEnabled"):
        value = values.get(attribute)
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
    return _shortcut_from(_read(
        element, ("AXMenuItemCmdChar", "AXMenuItemCmdVirtualKey",
                  "AXMenuItemCmdModifiers")))


def _shortcut_from(values) -> str:
    """The same spelling, read off attributes already in hand.

    lazydocs: ignore
    """
    char = values.get("AXMenuItemCmdChar") or ""
    virtual_key = values.get("AXMenuItemCmdVirtualKey")
    modifiers = values.get("AXMenuItemCmdModifiers")
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


class ActionsSource(CandidateSource):
    """Every action in `~/.keyhac/extensions/`, startable without a key.

    The half of the authoring loop a key binding never covered: a class lands
    in `extensions/`, works, and is runnable from here - the `config.py` edit
    that binds it to a key comes later, or never, for something used once a
    month.  That is also the answer to running out of keys, from the other
    side to `KeyBindingsSource`: one asks what the keys do, this asks what
    there is to run.

    **Listing does not import.** The catalogue is an AST parse
    (`keyhac.mcp.extensions.discover`), so a file is read and never executed
    to find out what is in it, and a module no `config.py` imports stays inert
    on disk. A class here runs at exactly one moment: when the operator picks
    it.

    What is offered is a `ThreadedAction` subclass, transitively and across
    files - not every callable class. The main thread services the keyboard
    hook and every window, so a list whose rows might block it is a list that
    can freeze the keyboard.

    A class needing constructor arguments is listed and says so rather than
    being hidden: an action missing from the list reads as Keyhac not seeing
    the file, which is a much worse thing to debug than a row that explains
    itself.
    """

    name = "Action"

    def __init__(self, name: str = None):
        """Build the source.

        Args:
            name: What a shared window shows beside these rows.
        """
        if name is not None:
            self.name = name

    def candidates(self):
        """lazydocs: ignore"""
        from keyhac.mcp.extensions import discover

        keymap = Keymap.get_instance()
        if keymap is None:
            return []
        try:
            found = discover(keymap.extensions_dir)
        except Exception:
            logger.debug("The extensions directory could not be read.")
            return []
        return [Candidate(icon="⚙", display=action.summary or action.name,
                          payload=action,
                          match_text=f"{action.name} {action.summary or ''}",
                          extras={"address": action.name,
                                  "required": action.required})
                for action in found]

    def badge(self, candidate) -> str:
        """lazydocs: ignore"""
        required = candidate.extras.get("required")
        if required:
            return f"needs {', '.join(required)}"
        return candidate.extras.get("address", "")

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        from keyhac.mcp.extensions import Loader

        action = candidate.payload
        if action.required:
            logger.error(
                f"{action.name} needs constructor arguments "
                f"({', '.join(action.required)}), so it cannot be started from "
                f"the list - give them defaults to run it from here.")
            return
        try:
            Loader().instantiate(action)()
        except Exception:
            logger.error(f"{action.name}: starting it failed.")


#: Roles worth offering as "things you can do in this window", in the two
#: OSes' own vocabularies.  Deliberately controls rather than content: the
#: measured tree of a heavy application is overwhelmingly AXGroup and
#: AXStaticText - the page, not the buttons on it.
_ACTIONABLE_ROLES = frozenset({
    # macOS
    "AXButton", "AXMenuButton", "AXPopUpButton", "AXCheckBox",
    "AXRadioButton", "AXTextField", "AXTextArea", "AXComboBox", "AXLink",
    "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXTab",
    "AXToolbarButton", "AXSearchField",
    # Windows
    "Button", "SplitButton", "CheckBox", "RadioButton", "Edit", "ComboBox",
    "Hyperlink", "Slider", "Spinner", "TabItem", "MenuItem", "ListItem",
    "TreeItem",
})

#: Node budget for one walk.  A generous bound rather than a tight one: the
#: rows stream, so a long walk costs patience rather than a frozen window,
#: and stopping early is the thing that silently hides a control.
_CONTROLS_MAX_NODES = 4000

#: Depth bound.  Measured: a Chromium web area puts its controls past depth
#: 20, so a shallow walk is not a cheaper walk, it is an empty one.
_CONTROLS_MAX_DEPTH = 40


class WindowControlsSource(CandidateSource):
    """Everything you could click in the front window, reachable by name.

    Discussion #112's original target, and the reason the window had to stop
    taking the keyboard focus: a list of "what is actionable here" that
    changes what is actionable by opening is no use to anybody.

    **It streams, because it is expensive.** Measured on macOS: a heavy
    application's tree is 3000 nodes and 460 ms, and filtering by role does
    not help - the walk is the cost, and reporting less of it changes
    nothing. So the walk yields as it goes and the first controls are on
    screen while the rest are still being found. Put it in a `ChooserPage` of its
    own all the same; it has real work to do on every invocation.

    **Only controls with a name are offered.** An icon-only button with no
    label, no description and no tooltip cannot be typed for - there is no
    text to filter on - so listing it would add a row nobody can reach. Where
    a name comes from is recorded on the candidate (`provenance`), because it
    decides what else can find the element: a control reachable only through
    its tooltip cannot be found by `find(name=...)` in an action either.
    """

    name = "Control"

    #: The walk reads *another* application's tree and touches nothing of
    #: Keyhac's, so it belongs on a worker - and this is the source that
    #: needed it most, being thousands of round trips into the other process.
    #: See MenuItemsSource.background.
    background = True

    def __init__(self, name: str = None):
        """Build the source.

        Args:
            name: What a shared window shows beside these rows.
        """
        if name is not None:
            self.name = name

    def candidates(self):
        """lazydocs: ignore"""
        # Not a generator, deliberately: finding the front window asks the OS
        # which application is frontmost, and that is a main-thread question.
        # Doing it here rather than after a `yield` is what lets the walk it
        # returns be handed to a worker - `candidates()` is always called on
        # the main thread, the generator it returns is not.
        keymap = Keymap.get_instance()
        if keymap is None:
            return []
        try:
            window = keymap.get_active_window()
        except Exception:
            window = None
        # `element`, not `native` - see MenuItemsSource._front_element: on
        # Windows `native` is the HWND wrapper, which has no tree to walk.
        element = getattr(window, "element", None) if window else None
        if element is None:
            logger.debug("No front window; there are no controls to read.")
            return []
        return _walk_controls(element)

    def badge(self, candidate) -> str:
        """lazydocs: ignore"""
        return candidate.extras.get("role", "")

    def on_chosen(self, candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        element, what = candidate.payload, candidate.display

        def act():
            if not (_click_control(element, what) or _press(element)):
                logger.error(f"{what}: the control refused to be pressed.")

        keymap = Keymap.get_instance()
        if keymap is None:
            act()
            return
        # One turn of the loop later, for two reasons that are the same
        # reason. The window this row was chosen from has been asked to close
        # but may still be on the screen, and it is the one thing guaranteed
        # to be over a control the user just pointed at - the hit test would
        # find it and refuse to click. And choosing happens inside the key
        # hook's callback, because the chooser reads its keys through the
        # hook, which is not where a click and a cross-process round trip
        # belong (ChooserAction.__call__ has the same note, with the
        # measurements).
        keymap.call_on_main_thread(act)


def _walk_controls(root):
    """Yield the named, actionable descendants of `root`, depth first.

    A generator for the same reason the menu walk is one: the window shows
    the first controls while the rest are still being found, and each yield
    is a place the walk can be abandoned when the window closes under it.
    `WindowControlsSource.background` is what decides where it runs.

    The `seen` set is not cycle paranoia. A table's cells are children of
    their row *and* of their column - the same element reached twice - so
    without it every cell of every table is reported twice
    (`keyhac/core/uitree.py` measured this).
    """
    seen = set()
    budget = [_CONTROLS_MAX_NODES]

    def walk(element, depth):
        if budget[0] <= 0 or depth > _CONTROLS_MAX_DEPTH:
            return
        budget[0] -= 1
        key = _identity_of(element)
        if key is not None:
            if key in seen:
                return
            seen.add(key)
        # The role first, and the rest only for an element the role says is
        # worth reading. `describe()` is a batched read of nine attributes and
        # most of a window's tree is groups and static text this will never
        # report, so describing everything pays eight reads per node to learn
        # one thing. Measured over VS Code's 4000 nodes: 588 ms against 346.
        try:
            role = element.role()
        except Exception:
            return
        if role in _ACTIONABLE_ROLES:
            try:
                described = element.describe()
            except Exception:
                described = {}
            name = described.get("name")
            if name:
                yield Candidate(
                    icon="⌖", display=name, payload=element,
                    match_text=f"{name} {role}",
                    rect=described.get("rect"),
                    provenance=described.get("name_source"),
                    extras={"role": role})
        try:
            children = element.children()
        except Exception:
            return
        for child in children or []:
            yield from walk(child, depth + 1)

    yield from walk(root, 0)


def _identity_of(element):
    key = getattr(element, "identity_key", None)
    if key is None:
        return None
    try:
        return key()
    except Exception:
        return None
