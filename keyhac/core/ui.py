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
from typing import Any, Callable

from keyhac.core import log
from keyhac.core.uitree import UINode, get_ui_tree

logger = log.getLogger("UI")


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

        **Say which window.** With no pattern this is the first *visible*
        window, which is not the focused one and is not even reliably the same
        one twice - it is whatever the platform enumerates first. A diagnostic
        written as `ui.window()` therefore reported on some other application
        entirely, every time, and looked like it was working. For the window
        the user is actually in, ask `keymap.get_active_window()` and wrap it
        with `ui.node()`; `ui.focused()` is the way in when the element
        matters more than the window.

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
            target: A node in the application, or None for the focused one,
                falling back to the frontmost window.  Any node will do; the
                request goes to its application.
            enable: False to give it back, which is polite and measurably
                works - Chrome returned to 59 nodes.

        Returns:
            True when the request was delivered - **not** that content
            appeared.  The two are different and this cannot tell them apart:
            Steam's client advertises `AXEnhancedUserInterface`, accepts the
            write, and still exposes a window with no children at all, in
            both its processes, measured 2026-08-24.  Some applications have
            no accessibility tree to be asked for.

            Verifying it would need a wait rather than a read.  Chromium
            builds the tree on its own schedule - half a second in the
            measurements this API came from - so checking immediately would
            report failure for content that was about to arrive, which is the
            fault `keyhac.core.fill.focus()` exists to avoid; and this runs on
            the event-loop thread, where waiting is not allowed.  So the
            honest answer here is the narrow one.
        """
        node = target or self.focused()
        element = getattr(node, "element", None)

        def apply():
            # Falling back to the window is not a nicety. An application that
            # exposes no accessibility tree has no focused element either -
            # which is exactly the application this call exists for - so
            # asking through focus alone could never reach the ones that need
            # it most. Steam's client reported no focused element at all and
            # no panes, and there was no way to ask it for any. The window is
            # there regardless, and any element in the application will do.
            start = element
            if start is None:
                window = self._keymap.get_active_window()
                start = getattr(window, "element", None)
            if start is None:
                return False
            application = self._application_of(start)
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
        accessibility switch has to be set on.

        An element that cannot say who its parent is stands for itself.  Both
        platform elements always can; this is for anything else handed in
        through `ui.node()`, and for the caller's promise that
        `enable_content_access()` is safe to call anywhere.
        """
        current = element
        for _ in range(32):
            climb = getattr(current, "parent", None)
            if climb is None:
                return current
            parent = climb()
            if parent is None:
                return current
            current = parent
        return current
