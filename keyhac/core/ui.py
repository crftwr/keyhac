"""`keymap.ui` - the entry point for actions that drive another application.

This is the whole surface an action needs, deliberately cut away from the
configuration API around it.  A config binds keys; an action reads and writes
somebody else's UI, and the two have almost nothing in common besides living in
the same file.  Keeping them apart is also what keeps `from keyhac import *`
from growing a `press`, a `focus` and a `find_element` that mean something only
inside an action.

    def run(self):                              # a ThreadedAction worker
        ui = keymap.ui
        window = ui.window(app="Safari")
        window.wait_for(identifier="results", message="the search to finish")
        for row in window.find_all(role="AXRow|DataItem"):
            print([cell.all_text for cell in row.children])

Everything here and on `UINode` reads the live UI and therefore dispatches to
the event-loop thread on its own.  Nothing about that is visible from an action,
which was the point: element access is main-thread-only, an action's pipeline
runs on a worker, and the first six actions written against the function-style
API wrapped nearly every line in a dispatch call.

CROSS-PLATFORM BY SHAPE, NOT BY DATA.  Every method here exists and behaves the
same way on Windows and macOS.  What does *not* carry across is the content of
the tree: a role is "AXTable" on macOS and "Table" on Windows, macOS keeps every
control's state in one value while Windows splits it between Value, ToggleState
and IsSelected, and neither one's element names mean anything to the other. An
action is written against a screen that was looked at first, so it is not
portable and should not pretend to be; the framework under it is.

The one genuinely one-sided thing is `enable_content_access()`, and it is
exposed here precisely so an action can call it unconditionally: macOS needs it
before a Chromium or Electron application will expose any content at all, and
Windows does not, where it returns False and does nothing.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any, Callable

from keyhac.core import log
from keyhac.core.uitree import UINode, get_ui_tree

logger = log.getLogger("UI")


#: How often to look at a postcondition while waiting for it.
_POLL = 0.05


@dataclass(frozen=True)
class Appears:
    """Satisfied when something matching this locator exists.

    The keywords are `find_elements`' - `role`, `name`, `value`,
    `identifier`, `text` - scoped by `within`. **`title` (with or without
    `app`) makes it a window locator instead**, because the thing a dialog's
    button opens is often a window rather than an element in one; that is the
    one ambiguity in the vocabulary and it is resolved by naming it here
    rather than by guessing.

    Satisfied *with a value*: the node it found, so the verb that waited for
    it can hand it back.
    """

    within: Any = None
    role: str = None
    name: str = None
    value: str = None
    identifier: str = None
    text: str = None
    app: str = None
    title: str = None
    #: Walk bounds (#81), on the same value rather than in a separate
    #: argument: a locator that needs a deeper walk to be found needs it
    #: everywhere it is used, and splitting the two apart is how a postcondition
    #: comes to look for something the finder could never have reached.
    max_depth: int = None
    max_nodes: int = None

    def _locator(self) -> dict:
        found = {k: v for k, v in (("role", self.role), ("name", self.name),
                                   ("value", self.value),
                                   ("identifier", self.identifier),
                                   ("text", self.text)) if v is not None}
        for bound in ("max_depth", "max_nodes"):
            if getattr(self, bound) is not None:
                found[bound] = getattr(self, bound)
        return found

    def baseline(self, ui):
        """lazydocs: ignore"""
        return None

    def check(self, ui, baseline):
        """lazydocs: ignore"""
        if self.title is not None or self.app is not None:
            found = ui.window(app=self.app, title=self.title)
            return found
        root = self.within if self.within is not None else ui.window()
        if root is None:
            return None
        return root.find(**self._locator())

    def __str__(self):
        parts = self._locator()
        if self.title is not None:
            parts["title"] = self.title
        return f"something matching {parts} to appear"


@dataclass(frozen=True)
class Gone:
    """Satisfied when `target` is no longer there.

    `target` is a node - the dialog you just dismissed - or an `Appears`,
    when what has to go is described rather than held. **Prefer the
    described form**: a held node can only be asked whether its own reference
    has died, which a platform answers well for a closed window and badly for
    a row that was merely removed from a list.
    """

    target: Any = None

    def baseline(self, ui):
        """lazydocs: ignore"""
        return None

    def check(self, ui, baseline):
        """lazydocs: ignore"""
        if isinstance(self.target, Appears):
            return not self.target.check(ui, None)
        node = self.target
        if node is None:
            return True
        # The platform's own answer first: "this reference points at nothing"
        # is a different fact from "this element has no role", and it is the
        # one that means gone.
        element = getattr(node, "element", None)
        is_stale = getattr(element, "is_stale", None)
        if is_stale is not None:
            try:
                if is_stale():
                    return True
            except Exception:
                return True
        try:
            return node.reread() is None
        except Exception:
            # StaleElement is the answer, not a failure: it is gone.
            return True

    def __str__(self):
        return f"{self.target!r} to go away"


@dataclass(frozen=True)
class Changed:
    """Satisfied when `target`'s own reading is not what it was.

    Its role, name, value and rectangle together, read once before the verb
    acts and again after: the tab that becomes selected, the field that fills
    in, the button whose caption flips.
    """

    target: Any = None

    def baseline(self, ui):
        """lazydocs: ignore"""
        return _reading(self.target)

    def check(self, ui, baseline):
        """lazydocs: ignore"""
        return _reading(self.target) != baseline

    def __str__(self):
        return f"{self.target!r} to change"


def _reading(node):
    if node is None:
        return None
    try:
        fresh = node.reread()
    except Exception:
        return None
    if fresh is None:
        return None
    return (fresh.role, fresh.name, fresh.value, fresh.rect)


class UI:
    """The action-facing view of the desktop.  Reached as `keymap.ui`."""

    def __init__(self, keymap):
        """Built by the Keymap; actions never construct one.

        lazydocs: ignore
        """
        self._keymap = keymap

    # -- finding somewhere to start ------------------------------------------

    def focused(self) -> UINode | None:
        """The element with keyboard focus right now, as a node.

        The cheapest root there is: a key binding already told you which
        application and which field the user meant (design document §3.2).

        **Asked each time, not remembered.** This used to hand back
        `keymap.focus`, which is a snapshot taken while a key was being
        dispatched - so an action that closed a window and waited for focus to
        land somewhere else never saw it move, and kept being handed the
        destroyed element, or the application that no longer had a window.
        Polling did not help, because polling produces no keystrokes and only a
        keystroke refreshed it (issue #44).

        `keymap.focus` stays what it was, on purpose. Deciding which key table
        applies to a keystroke needs the focus *that keystroke* was aimed at,
        and re-reading it there would race the key it is dispatching. The two
        are different questions; this is the one an action is asking.

        Returns:
            The focused element, or None when nothing has focus or the
            platform could not say. None is an answer - a stale element that
            fails every attribute read is not.
        """
        provider = self._keymap._focus_provider
        return self.on_main_thread(
            lambda: self.node(provider.get_focused_element()))

    def window(self, app: str = None, title: str = None,
               class_name: str = None) -> UINode | None:
        """A top-level window, as a node to search inside.

        Matches exactly like `keymap.find_window` and `define_keytable`:
        case-insensitive fnmatch, "|" alternation, ".exe" optional on Windows.

        Args:
            app: Application name pattern.
            title: Window title pattern.
            class_name: Win32 class name pattern (Windows only).

        Returns:
            The window's element as a node, or None when nothing matched.
        """
        window = self._keymap.find_window(app=app, title=title,
                                          class_name=class_name)
        return self.node(getattr(window, "element", None)) if window else None

    def windows(self, app: str = None, title: str = None) -> list[UINode]:
        """Every matching top-level window, as nodes.

        For the cases where "the window" is ambiguous - a browser with several
        windows open, or an application whose settings live in a second one.
        """
        from keyhac.core.focus import match_window_fields

        out = []
        for window in self._keymap.list_windows():
            if not match_window_fields(window, app=app, title=title):
                continue
            node = self.node(getattr(window, "element", None))
            if node is not None:
                out.append(node)
        return out

    def at_point(self, x: float, y: float) -> UINode | None:
        """The element under a screen point, in whichever application owns it.

        The cheap way into the text layer: the pointer is usually already over
        the line the user means (design document §6).
        """
        element = self._platform_elements()
        if element is None:
            return None
        return self.node(element.element_at_point(x, y))

    def node(self, element) -> UINode | None:
        """Wrap a platform element as a node, reading nothing below it.

        The escape hatch for an element obtained some other way - through
        `keymap.focus.element`, or a platform call this API does not cover.
        """
        if element is None:
            return None
        if isinstance(element, UINode):
            return element
        return self.on_main_thread(lambda: get_ui_tree(element, max_depth=0))

    # -- waiting -------------------------------------------------------------

    def wait(self, condition: Callable[[], Any], timeout: float = 10.0,
             message: str | None = None, interval: float | None = None) -> Any:
        """Block until `condition()` is truthy, and return what it returned.

        For a wait that is not "an element appeared" or "an element went away"
        - those are `node.wait_for()` and `node.wait_until_gone()`. Never
        `sleep`: a fixed delay passes on the machine it was written on, and on
        a faster one it fails *silently*, acting on a screen that has not
        arrived.

        Raises:
            WaitTimeout: The condition never became true.
        """
        from keyhac.core.wait import wait_for
        return wait_for(condition, timeout=timeout, message=message,
                        interval=interval)

    # -- verbs ---------------------------------------------------------------

    #: The postconditions, on the one entry point rather than in the config's
    #: namespace: `ui.Appears(...)`. Three importable names is a decision the
    #: tests pin (`test_only_three_action_names_are_importable`), and a verb
    #: layer that needed four import lines to be written correctly would be a
    #: worse thing to generate, not a better one.
    Appears = Appears
    Gone = Gone
    Changed = Changed

    def click(self, within=None, until=None, timeout: float = 10.0,
              retry_every: float = 2.0, **locator):
        """Find one control and press it, and say what "it worked" means.

        ```python
        ui.click(role="Button", name="Save", within=dialog,
                 until=Appears(identifier="save-panel"))
        ```

        **The platform's answer is not evidence.** An accessibility press is
        accepted by applications that then do nothing with it - measured, an
        `AXPress` on a control drawn by a Chromium application returns success
        and moves nothing unless that application has been told an assistive
        client is present. So `until` is how a caller says what to look for,
        and the press is repeated every `retry_every` until it holds.

        **Without `until` it presses once.** A blind retry double-acts -
        double-save, double-submit - so the retry is the caller's to ask for,
        and code that does not ask is visibly the weaker code rather than
        silently the unlucky code.

        Args:
            within: Where to look; the focused window by default.
            until: What makes it true - `Appears`, `Gone`, `Changed`, or a
                callable. None presses once and returns.
            timeout: Seconds before giving up, in total.
            retry_every: Seconds to watch the postcondition before pressing
                again.
            **locator: `find_elements` keywords - role, name, value,
                identifier, text.

        Returns:
            Whatever `until` was satisfied with (an `Appears` hands back the
            node it found), or the node that was pressed when there is no
            `until`.

        Raises:
            WaitTimeout: The target never appeared, or the postcondition never
                held.
            StaleElement: The target was there and had gone by the time it was
                pressed.
        """
        from keyhac.core.fill import press

        target = self.wait(
            lambda: (within if within is not None else self.window()) is not None
            and (within if within is not None else self.window()).find(**locator),
            timeout=timeout,
            message=f"a control matching {locator} to appear")
        # The precondition #61 asks for: the screen may have moved between
        # finding it and acting on it, and a press aimed at what used to be
        # there is the silent wrong thing this API exists to refuse.
        target.reread()
        return self._until(lambda: press(target), until, timeout, retry_every,
                           what=f"clicking {locator}") or target

    def send_key(self, keys: str, until=None, timeout: float = 10.0,
                 retry_every: float = 2.0):
        """Send a key expression, and say what "it arrived" means.

        ```python
        ui.send_key("Cmd-P", until=Appears(title="Print"))
        ```

        Nothing can confirm a keystroke arrived - the application may be
        starting, may have a window of its own in front, may be busy - which
        is why every action that sends one grows a retry loop of its own.
        This is that loop, once.

        Args:
            keys: A key expression, as `InputContext.send_key` takes it.
            until: What makes it true; None sends it once.
            timeout: Seconds before giving up, in total.
            retry_every: Seconds to watch the postcondition before sending
                again.

        Returns:
            Whatever `until` was satisfied with, or None.

        Raises:
            WaitTimeout: The postcondition never held.
        """
        def send():
            with self._keymap.get_input_context() as ctx:
                ctx.send_key(keys)

        return self._until(send, until, timeout, retry_every,
                           what=f"sending {keys!r}")

    def _until(self, act, until, timeout: float, retry_every: float, what: str):
        """Act, watch, act again - the one loop the verbs share.

        lazydocs: ignore
        """
        from keyhac.core.wait import WaitTimeout, _refuse_to_block_the_loop

        if until is None:
            act()
            return None
        _refuse_to_block_the_loop("click/send_key with until=")
        check = until if callable(until) else None
        baseline = None if check else until.baseline(self)
        deadline = time.monotonic() + timeout
        attempts = 0
        while True:
            act()
            attempts += 1
            edge = min(time.monotonic() + retry_every, deadline)
            while True:
                got = check() if check else until.check(self, baseline)
                if got:
                    return got
                if time.monotonic() >= edge:
                    break
                time.sleep(_POLL)
            if time.monotonic() >= deadline:
                raise WaitTimeout(
                    f"{what} did not take: waited {timeout:.0f}s for "
                    f"{until} over {attempts} attempts")

    # -- writing -------------------------------------------------------------

    @contextlib.contextmanager
    def preserve_clipboard(self):
        """Put the clipboard back the way it was afterwards.

        `node.set_text()` already does this around its own paste; this is for
        an action that uses the clipboard for something else.
        """
        from keyhac.core.fill import preserve_clipboard
        with preserve_clipboard():
            yield

    @contextlib.contextmanager
    def content_access(self, target: UINode | None = None):
        """Turn content access on for the block, and hand it back afterwards.

        ```python
        with self.ui.content_access():
            ...
        ```

        `enable_content_access()` on its own is the one call that changes
        another application and leaves it changed: nothing turns it off, so
        the flag outlives the action, the key press and the session. That is
        not tidiness - it decides behaviour. A press into a Chromium
        application's *content* is live only while the flag is set, so an
        action that leaves it on makes the next unrelated press work for
        reasons nobody chose, and one that never set it makes the same press
        do nothing at all while reporting success.

        **It does not wait for the application to act on it.** Measured on VS
        Code: the write is accepted at once and the tree is readable at once,
        but a *press* only starts working about two seconds later. Waiting
        here would put that stall in front of every action, to buy what a
        verified retry gets for nothing - act, check the postcondition, act
        again (discussion #98). Reading, which is what an action does first,
        needs no wait at all.

        Nested blocks are counted, so an inner one does not hand back what an
        outer one still needs. Two different applications at once is not
        something this counts - an action works in one at a time.

        Args:
            target: A node in the application, or None for the focused one.

        Yields:
            Whether the platform did anything (False on Windows, which needs
            nothing equivalent).
        """
        depth = getattr(self, "_content_access_depth", 0)
        enabled = self.enable_content_access(target, True) if depth == 0 else False
        self._content_access_depth = depth + 1
        try:
            yield enabled or depth > 0
        finally:
            self._content_access_depth -= 1
            if self._content_access_depth == 0 and enabled:
                self.enable_content_access(target, False)

    # -- platform differences, exposed so they can be ignored ----------------

    def enable_content_access(self, target: UINode | None = None,
                              enable: bool = True) -> bool:
        """Ask a Chromium or Electron application to expose its content.

        **macOS only, and safe to call anywhere.** Chrome, Edge, VS Code and
        Slack build no accessibility tree until an assistive client asks: a
        loaded page measured 59 nodes of browser chrome with no document in it,
        and 119 with every field addressable once asked. Windows needs nothing
        equivalent - Chromium enables its renderer tree when a UIA client
        attaches - so this returns False there, and an action calls it either
        way rather than branching.

        Args:
            target: A node in the application, or None for the focused one.
                Any node will do; the request goes to its application.
            enable: False to give it back, which is polite and measurably
                works - Chrome returned to 59 nodes.

        Returns:
            True when the platform did something.
        """
        node = target or self.focused()
        element = getattr(node, "element", None)
        if element is None:
            return False

        def apply():
            application = self._application_of(element)
            setter = getattr(application, "set_manual_accessibility", None)
            if setter is None:
                return False
            setter(enable)
            return True

        return bool(self.on_main_thread(apply))

    # -- threads --------------------------------------------------------------

    def on_main_thread(self, func: Callable[[], Any]) -> Any:
        """Run `func` on the event-loop thread and return its result.

        Every method here already does this, so an action needs it only to make
        several reads atomic with respect to a UI that is moving underneath -
        or to call a platform element method this API does not wrap.
        """
        from keyhac.core.wait import evaluate_on_main_thread
        return evaluate_on_main_thread(func)

    # -- internals -------------------------------------------------------------

    def _platform_elements(self):
        """The platform's UIElement class, or None where there is not one."""
        if self._keymap.platform == "mac":
            from keyhac.platform.mac.uielement import UIElement
            return UIElement
        if self._keymap.platform == "windows":
            from keyhac.platform.win.uielement import UIElement
            return UIElement
        return None

    @staticmethod
    def _application_of(element):
        """Walk up to the application element, which is what the Chromium
        accessibility switch has to be set on."""
        current = element
        for _ in range(32):
            parent = current.parent()
            if parent is None:
                return current
            current = parent
        return current
