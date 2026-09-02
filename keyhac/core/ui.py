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


@dataclass(frozen=True)
class Locator:
    """Which element, as a value.

    ```python
    SAVE_PANEL = Locator(identifier="save-panel", max_depth=2)
    ui.click(SAVE, within=dialog, until=ui.Appears(SAVE_PANEL, within=dialog))
    ```

    **Lifted out of the call so that a repair is a patch rather than a
    rewrite.** The same selector is written four times in one real action -
    the act, its postcondition, the dismissal and a read - and as keyword
    arguments those are four places anything fixing it has to find and
    understand. As a value they are one, and its `repr` is a Python literal:
    storable, diffable, and replaceable without parsing code (discussion #98).

    The keywords are `find_elements`' own, so nothing new has to be learned to
    write one, and the walk bounds ride along because a locator that needs a
    deeper walk needs it everywhere it is used.

    **`within` is deliberately not here.** Scope is a live node, and a value
    holding a handle is not a value any more - it cannot be written down, kept
    or compared. It stays an argument of the call that uses the locator.

    `predicate` can be here, and is the one field nothing can patch. That is
    worth showing rather than hiding: a caller reading a locator can see
    exactly which part of it is opaque.
    """

    role: str = None
    name: str = None
    value: str = None
    identifier: str = None
    text: str = None
    predicate: Any = None
    max_depth: int = None
    max_nodes: int = None

    def criteria(self) -> dict:
        """The `find_elements` keywords this stands for.

        lazydocs: ignore
        """
        return {field: getattr(self, field)
                for field in ("role", "name", "value", "identifier", "text",
                              "predicate", "max_depth", "max_nodes")
                if getattr(self, field) is not None}

    def __str__(self):
        return f"something matching {self.criteria()}"


class Condition:
    """What a "not yet" slot takes, besides a plain callable.

    `ui.wait()`, `given=` and `until=` all accept the same two types - a
    zero-argument callable, or one of these - and that is the point: the
    values compose in every slot, so the only thing an author decides is
    *which slot*, which is the question of who causes the thing being waited
    for. See `doc/dev/design-notes.md`.

    A subclass answers `check(ui)` with something truthy - the node it found,
    where there is one, so waiting for a thing and receiving it are one
    motion. `postcondition` is False for a value that must not be used as
    one, and `wait()` is here for the value whose question is about a stretch
    of time rather than an instant, which polling a predicate cannot ask.
    """

    #: False for a value that cannot honestly say "my act produced this".
    postcondition = True

    def check(self, ui):
        """Whether it holds, and what it holds *with*.

        lazydocs: ignore
        """
        raise NotImplementedError

    def wait(self, ui, timeout: float, message: str = None):
        """Block until it holds. Polling `check` unless a value knows better.

        lazydocs: ignore
        """
        from keyhac.core.wait import wait_for
        return wait_for(lambda: self.check(ui), timeout=timeout,
                        message=message or str(self))


@dataclass(frozen=True)
class Appears(Condition):
    """Satisfied when something matching this locator exists.

    **With a locator it is an element question, without one a window
    question**, which is the whole rule: the thing a dialog's button opens is
    often a window rather than an element in one, and `app` / `title` name it.
    Given a locator *and* `app` / `title`, it looks for the element in those
    windows - because *which* window is often what the action cannot answer
    first. `within` scopes it to one node instead.

    Satisfied *with a value*: the node it found, so the verb that waited for
    it can hand it back.
    """

    locator: Locator = None
    within: Any = None
    app: str = None
    title: str = None

    def _locator(self) -> dict:
        return self.locator.criteria() if self.locator is not None else {}

    def check(self, ui):
        """lazydocs: ignore"""
        criteria = self._locator()
        if not criteria:
            # No locator: the question is about a window itself.
            return ui.window(app=self.app, title=self.title)
        if self.within is not None:
            return self.within.find(**criteria)
        if self.app is not None or self.title is not None:
            # A control somewhere in this application, when *which* window is
            # the question the action cannot answer first: the tab strip is in
            # the browser window, but the application also owns a print dialog
            # and an untitled download popup, and they come and go.
            for window in ui.windows(app=self.app, title=self.title):
                found = window.find(**criteria)
                if found:
                    return found
            return None
        root = ui.window()
        return root.find(**criteria) if root is not None else None

    def __str__(self):
        parts = dict(self._locator())
        if self.title is not None:
            parts["title"] = self.title
        return f"something matching {parts} to appear"


@dataclass(frozen=True)
class Front(Condition):
    """Satisfied when the front window is this one.

    The precondition that is not about the target, and the one a verb cannot
    fold without a name for it: an action sends Cmd-P to *the browser*, and
    after a save the application's own download popup owns the front for a few
    seconds and swallows it. Waiting for the right window to be in front is
    the difference between a retry that converges and one that types into
    whatever is there.
    """

    app: str = None
    title: str = None

    def check(self, ui):
        """lazydocs: ignore"""
        from keyhac.core.focus import match_window_fields
        provider = getattr(ui._keymap, "window_provider", None)
        active = provider.get_active_window() if provider is not None else None
        if active is None:
            return None
        if not match_window_fields(active, app=self.app, title=self.title):
            return None
        return ui.node(getattr(active, "element", None))

    def __str__(self):
        return f"the front window to be {self.app or ''} {self.title or ''}".strip()


@dataclass(frozen=True)
class Gone(Condition):
    """Satisfied when `target` is no longer there.

    `target` is a `Locator` (with `within`, if it is scoped), an `Appears`, or
    a node - the dialog you just dismissed. **Prefer the described forms**: a
    held node can only be asked whether its own reference has died, which a
    platform answers well for a closed window and badly for a row that was
    merely removed from a list.
    """

    target: Any = None
    within: Any = None

    def check(self, ui):
        """lazydocs: ignore"""
        if isinstance(self.target, Locator):
            return not Appears(self.target, within=self.within).check(ui)
        if isinstance(self.target, Appears):
            return not self.target.check(ui)
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
class Reads(Condition):
    """Satisfied when `target` reads as the values given.

    **The postcondition to reach for.** The rule the authoring skill states -
    *wait for the state you expect, not for the old state to change* - is this
    value: "it differs from what I captured" and "the result arrived" coincide
    only when the new state happens to differ, and a transform can be the
    identity. A translation whose output equals its input leaves a `Changed`
    waiting forever with the screen already correct.

    `value` is compared as text against the same patterns `find` takes, so
    `value="True"` matches a macOS AXValue of `True` and a Windows toggle
    state of `"True"` without the caller knowing which it got.
    """

    target: Any = None
    role: str = None
    name: str = None
    value: Any = None

    def check(self, ui):
        """lazydocs: ignore"""
        from keyhac.core.uitree import match_pattern, match_role

        if self.target is None:
            return None
        try:
            fresh = self.target.reread(max_depth=0)
        except Exception:
            return None
        if fresh is None:
            return None
        if self.role is not None and not match_role(fresh.role, self.role):
            return None
        if self.name is not None and not match_pattern(fresh.name, self.name):
            return None
        if self.value is not None and not match_pattern(str(fresh.value),
                                                        str(self.value)):
            return None
        return fresh

    def __str__(self):
        wanted = {k: v for k, v in (("role", self.role), ("name", self.name),
                                    ("value", self.value)) if v is not None}
        return f"{self.target!r} to read as {wanted}"


@dataclass(frozen=True)
class Stable(Condition):
    """Satisfied when a subtree has stopped changing.

    For the re-render nobody can name: a dependent field repopulating, a table
    repainting after a sort. **Say the state you expect wherever you can** -
    `Appears(text="page 2")`, `Reads(row, value=…)` - because this is the
    escape for what cannot be named, and an escape is easy to reach for.

    **It is a precondition, never a postcondition**, and the API refuses it as
    one. Quiet is the *absence* of change, so it is satisfied by the calm
    before an act's effect starts as readily as by the calm after: as an
    `until=` it has a race built into its meaning. As a `given=`, or in a
    `wait()` of its own before a read, it says something true - "do not act
    into a screen that is still moving", "let it finish, then read".

    Its question is about a stretch of time rather than an instant, so unlike
    the other values it cannot be asked by polling a predicate; it brings its
    own wait.
    """

    postcondition = False

    within: Any = None
    quiet: float = 0.3
    max_depth: int = None
    max_nodes: int = None

    def check(self, ui):
        """lazydocs: ignore"""
        # A single look cannot answer "has it been quiet"; wait() is the
        # honest form, and this keeps a stray poll from lying either way.
        raise TypeError(
            "Stable cannot be checked at an instant - it asks about a stretch "
            "of time. Use it in ui.wait() or as given=, which wait for it.")

    def wait(self, ui, timeout: float, message: str = None):
        """lazydocs: ignore"""
        from keyhac.core.wait import wait_for_stable

        root = self.within if self.within is not None else ui.window()
        if root is None:
            return None
        bounds = {}
        if self.max_depth is not None:
            bounds["max_depth"] = self.max_depth
        if self.max_nodes is not None:
            bounds["max_nodes"] = self.max_nodes
        wait_for_stable(root, quiet=self.quiet, timeout=timeout, **bounds)
        return root

    def __str__(self):
        where = self.within if self.within is not None else "the front window"
        return f"{where!r} to stop changing for {self.quiet}s"


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

    def wait(self, condition: Condition | Callable[[], Any],
             timeout: float = 10.0, message: str | None = None,
             interval: float | None = None) -> Any:
        """Block until `condition()` is truthy, and return what it returned.

        For a wait that is not "an element appeared" or "an element went away"
        - those are `node.wait_for()` and `node.wait_until_gone()`. Never
        `sleep`: a fixed delay passes on the machine it was written on, and on
        a faster one it fails *silently*, acting on a screen that has not
        arrived.

        `condition` may also be an `Appears` / `Front` / `Gone` / `Reads` / `Stable`
        rather than a callable - the same question without the lambda, and
        without the predicate helper an action grows to hold the lambda.

        **This is the wait for what something else causes** - a file
        appearing, a job finishing, a window someone else opens - where
        waiting is the whole strategy because nothing you could do would
        help. What your own act causes is a verb's `until=`; what has to be
        true before your act goes out is its `given=`.

        Raises:
            WaitTimeout: The condition never became true.
        """
        from keyhac.core.wait import wait_for
        if not callable(condition):
            # The value knows how to wait for itself: polling a predicate,
            # unless its question is about a stretch of time.
            return condition.wait(self, timeout, message)
        return wait_for(condition, timeout=timeout, message=message,
                        interval=interval)

    # -- verbs ---------------------------------------------------------------

    #: The postconditions, on the one entry point rather than in the config's
    #: namespace: `ui.Appears(...)`. Three importable names is a decision the
    #: tests pin (`test_only_three_action_names_are_importable`), and a verb
    #: layer that needed four import lines to be written correctly would be a
    #: worse thing to generate, not a better one.
    Locator = Locator
    Appears = Appears
    Front = Front
    Gone = Gone
    Reads = Reads
    Stable = Stable

    def click(self, locator: Locator = None, within=None, node=None,
              given: Condition | Callable[[], Any] = None,
              until: Condition | Callable[[], Any] = None,
              timeout: float = 10.0, retry_interval: float = 2.0):
        """Find one control and press it, and say what "it worked" means.

        ```python
        SAVE = ui.Locator(role="Button", name="Save")
        ui.click(SAVE, within=dialog, until=ui.Appears(SAVE_PANEL))
        ```

        **The platform's answer is not evidence.** An accessibility press is
        accepted by applications that then do nothing with it - measured, an
        `AXPress` on a control drawn by a Chromium application returns success
        and moves nothing unless that application has been told an assistive
        client is present. So `until` is how a caller says what to look for,
        and the press is repeated every `retry_interval` until it holds.

        **Without `until` it presses once.** A blind retry double-acts -
        double-save, double-submit - so the retry is the caller's to ask for,
        and code that does not ask is visibly the weaker code rather than
        silently the unlucky code.

        **The act is the whole ladder** (`keyhac.core.act`): a click where the
        screen can prove the control is at the point about to be clicked, the
        platform's press behind it, the focus last. An action never writes the
        fallback itself, for the same reason `set_text` owns paste-then-type
        rather than leaving it to every caller.

        Args:
            locator: Which control, as a value (`ui.Locator`).
            within: Where to look; the focused window by default. Not part of
                the locator, because scope is a live node and a value holding
                a handle stops being one.
            node: A node already in hand, instead of a locator - the third row
                of a list an earlier step enumerated is a thing no locator
                says well.
            given: What must hold before each attempt - state of the world
                somebody else has to have arranged, which this waits for and
                never causes. It is re-checked before *every* attempt, and
                that is the whole reason it is a parameter: with no `until`
                it is only sugar for `wait()` then the call, but with one, a
                hoisted `wait()` guards the first attempt and nothing after
                it. It also fails distinctly - a precondition that never held
                and an act that did not take are different diagnoses.
            until: What makes it true - what *this act* produces, which is
                the definition of it having landed, and a separate clause only
                because the platform lies about success. Waiting here for
                something the act does not cause fires it again and again into
                a door that is not open. None presses once and returns.
            timeout: Seconds before giving up, in total.
            retry_interval: Seconds to watch the postcondition before pressing
                *again* - the only rate here, because how often to *look* is
                `wait_for`'s backing-off default and cannot be got expensively
                wrong, while pressing again can: too short, and a dialog that
                takes three seconds to open gets pressed three times.
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
        from keyhac.core.act import act_on

        target = self._target(locator, within, node, timeout)
        # The precondition #61 asks for: the screen may have moved between
        # finding it and acting on it, and a press aimed at what used to be
        # there is the silent wrong thing this API exists to refuse.
        target.reread()

        def act():
            # AX from a worker goes through the loop thread; the ladder reads
            # geometry, hit-tests and injects a click, all of which are its.
            return self.on_main_thread(lambda: act_on(target.element))

        return self._until(act, until, timeout, retry_interval,
                           what=f"clicking {locator or target!r}",
                           given=given) or target

    def activate(self, app: str = None, title: str = None,
                 timeout: float = 10.0, retry_interval: float = 2.0):
        """Bring a window to the front, and wait until it really is.

        ```python
        ui.activate(app="Google Chrome")
        ```

        An act with a postcondition, which is what makes it a verb rather
        than a wrapper: asking a window to activate is not the same as it
        being in front, and the difference is where a keystroke goes. It was
        also the last thing an action had to reach around this API to do -
        `keymap.find_window(...).activate()` on the loop thread, by hand.

        Args:
            app: Application pattern, as `window()` takes it.
            title: Window title pattern.
            timeout: Seconds before giving up.
            retry_interval: Seconds to watch before asking again - an
                application starting up can take more than one ask.

        Returns:
            The front window's node.

        Raises:
            WaitTimeout: It never came to the front.
        """
        def act():
            def bring():
                window = self._keymap.find_window(app=app, title=title)
                return window.activate() if window is not None else False
            return self.on_main_thread(bring)

        return self._until(act, Front(app=app, title=title), timeout,
                           retry_interval, what=f"activating {app or title!r}")

    def fill(self, text: str, locator: Locator = None, within=None, node=None,
             given: Condition | Callable[[], Any] = None,
             until: Condition | Callable[[], Any] = None,
             timeout: float = 10.0, retry_interval: float = 2.0):
        """Find one field and write `text` into it.

        ```python
        ui.fill("REC-001", ui.Locator(identifier="record-id"), within=form)
        ```

        `set_text` already focuses, verifies the focus landed, writes, and
        reads the value back, raising `FillFailed` naming what each mechanism
        did - so this verb rarely needs an `until`. What it adds is the
        locator, the precondition and the one shape every step has.

        **A `FillFailed` is not retried**, and that is deliberate: it means
        the write happened and the read-back disagreed, so doing it again is
        the double-act hazard. A field that is not ready yet is a `given=`.

        Args:
            text: What to write.
            locator: Which field, as a value.
            within: Where to look; the focused window by default.
            node: A field already in hand, instead of a locator.
            given: What must hold before the write.
            until: What makes it true, when the read-back is not the whole
                story - a form that only enables Save once the field is
                valid.
            timeout: Seconds before giving up, in total.
            retry_interval: Seconds to watch `until` before writing again.

        Returns:
            Whatever `until` was satisfied with, or the field.

        Raises:
            FillFailed: Every mechanism was tried and the value did not stick.
            WaitTimeout: The field never appeared, or `until` never held.
        """
        target = self._target(locator, within, node, timeout)
        target.reread()
        return self._until(lambda: target.set_text(text), until, timeout,
                           retry_interval, what=f"filling {locator or target!r}",
                           given=given) or target

    def scroll(self, locator: Locator = None, within=None, node=None,
               by: str = "down", amount: float = 3.0,
               given: Condition | Callable[[], Any] = None,
               until: Condition | Callable[[], Any] = None,
               timeout: float = 10.0, retry_interval: float = 0.4):
        """Turn the wheel over a view until something shows up in it.

        ```python
        row = ui.scroll(node=table,
                        until=ui.Appears(ui.Locator(text="REC-042"),
                                         within=table))
        ```

        **For the rows that are not there until you scroll.** A virtualised
        list has no element for a row it has not drawn, so no amount of
        looking finds it and no bound on the walk helps - the only way to read
        the fortieth row is to bring it into view. That is what this is for,
        and it is why it is a verb of its own rather than something `click`
        does on the way (which it also does, for a control it is about to
        press).

        Scrolling past the target is the hazard, so `retry_interval` is short
        and `amount` modest: the postcondition is looked at between turns, not
        after a page of them.

        Args:
            locator: Which view to scroll.
            within: Where to look for it; the focused window by default.
            node: The view already in hand, instead of a locator.
            by: `"down"` or `"up"`.
            amount: Wheel notches per turn.
            given: What must hold before each turn.
            until: What makes it true. None turns the wheel once.
            timeout: Seconds before giving up, in total.
            retry_interval: Seconds to watch before turning again.

        Returns:
            Whatever `until` was satisfied with, or the view.

        Raises:
            WaitTimeout: It never showed up.
        """
        from keyhac.core.act import scroll as turn

        view = (self._target(locator, within, node, timeout)
                if (locator is not None or node is not None) else self.window())
        notches = -abs(amount) if by == "down" else abs(amount)

        def act():
            return self.on_main_thread(lambda: turn(view.element, notches))

        return self._until(act, until, timeout, retry_interval,
                           what=f"scrolling {by}", given=given) or view

    def choose(self, *path: str, given: Condition | Callable[[], Any] = None,
               until: Condition | Callable[[], Any] = None,
               timeout: float = 10.0, retry_interval: float = 2.0):
        """Pick a command out of the menu bar by its path.

        ```python
        ui.choose("File", "Export", "As PDF…")
        ```

        **macOS only, and that is a fact about the platform rather than a gap
        here.** There the menu bar is an OS-level part, one per application,
        readable in full *while it is closed* - so this finds the leaf in the
        closed tree and presses that, opening nothing on the way. Windows has
        no menu bar in this sense (`doc/dev/design-notes.md`), and this says
        so rather than pretending.

        Args:
            *path: Menu names from the bar down to the command.
            given: What must hold before the command is pressed.
            until: What makes it true - a dialog appearing, usually.
            timeout: Seconds before giving up, in total.
            retry_interval: Seconds to watch `until` before pressing again.

        Returns:
            Whatever `until` was satisfied with, or the menu item.

        Raises:
            WaitTimeout: The path was not there.
            ValueError: This platform has no menu bar.
        """
        from keyhac.core.act import act_on

        if not path:
            raise ValueError("choose() needs a menu path")
        item = self.wait(lambda: self._menu_item(path), timeout=timeout,
                         message=f"the menu path {' > '.join(path)}")
        return self._until(lambda: self.on_main_thread(lambda: act_on(item.element)),
                           until, timeout, retry_interval,
                           what=f"choosing {' > '.join(path)}",
                           given=given) or item

    def _menu_item(self, path):
        """The node at `path` in the closed menu bar, or None.

        lazydocs: ignore
        """
        def find():
            from keyhac.core.uitree import get_ui_tree, match_pattern

            focused = self.focused()
            element = getattr(focused, "element", None)
            bar = getattr(element, "menu_bar", lambda: None)() if element else None
            if bar is None:
                raise ValueError(
                    "this platform has no menu bar to choose from - on Windows "
                    "a command lives in the window, so find and click it")
            node = get_ui_tree(bar, max_depth=2 * len(path) + 2)
            for name in path:
                node = next((child for child in node.walk()
                             if match_pattern(child.name, name)), None)
                if node is None:
                    return None
            return node

        return self.on_main_thread(find)

    def _target(self, locator, within, node, timeout):
        """The element a verb is about: the one in hand, or the one found.

        `node` excludes the other two rather than quietly outranking them.
        Silently dropping an argument somebody wrote is the failure this whole
        layer exists to refuse, and "I passed a locator and it used a stale
        node instead" is exactly that failure.

        lazydocs: ignore
        """
        if node is not None:
            if locator is not None or within is not None:
                raise ValueError(
                    "node= says which element, so locator= and within= have "
                    "nothing left to say - pass one or the other")
            return node
        return self._locate(within, locator, timeout)

    def _locate(self, within, locator, timeout):
        """Wait for one element matching `locator`, under `within` or the
        focused window.

        lazydocs: ignore
        """
        if locator is None:
            raise ValueError("no locator and no node: say which element")
        criteria = locator.criteria()
        return self.wait(
            lambda: (within if within is not None else self.window()) is not None
            and (within if within is not None else self.window()).find(**criteria),
            timeout=timeout, message=f"{locator} to appear")

    def send_key(self, keys: str,
                 given: Condition | Callable[[], Any] = None,
                 until: Condition | Callable[[], Any] = None,
                 timeout: float = 10.0, retry_interval: float = 2.0):
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
            given: What must hold before each attempt - `Front` is the one
                this verb is usually given, because a keystroke goes to
                whatever is in front rather than to whatever you meant, and
                what was in front when the first attempt went out need not
                be in front for the second.
            until: What makes it true; None sends it once.
            timeout: Seconds before giving up, in total.
            retry_interval: Seconds to watch the postcondition before sending
                *again*; how often to look is not a parameter, for the reason
                `click` gives.

        Returns:
            Whatever `until` was satisfied with, or None.

        Raises:
            WaitTimeout: The postcondition never held.
        """
        def send():
            with self._keymap.get_input_context() as ctx:
                ctx.send_key(keys)

        return self._until(send, until, timeout, retry_interval,
                           what=f"sending {keys!r}", given=given)

    def _until(self, act, until, timeout: float, retry_interval: float, what: str,
               given=None):
        """Wait for the precondition, act, watch, act again - the one loop
        the verbs share, and the one an action no longer writes.

            deadline = now + timeout
            repeat:
                wait for given()                    # the gate
                act()                               # the one costly line
                watch until() for retry_interval       # or until the deadline
                if it held: return what it held with
            fail

        **One rate, deliberately.** Looking is `wait_for`'s business and uses
        its backing-off default, which is right for a watch that wants to
        notice a fast answer at once and cost little when the answer is slow.
        Only `retry_interval` is a parameter, because only acting again can be
        expensive to get wrong: too small and a print dialog that takes three
        seconds gets three Cmd-Ps and opens three times.

        **The naming rule, for whoever adds the next one.** A bare `interval`
        is the *polling* one - that is what it means in `UI.wait`, which is
        released and settles the question - and every other interval carries
        a word saying which it is. So `retry_interval` here, and if looking
        ever does need exposing it arrives as `interval` on these verbs too,
        beside rather than against the released name. Two unqualified names
        for two different rates is what made this pair unreadable when it was
        `interval` and `retry_every`.

        lazydocs: ignore
        """
        from keyhac.core.wait import (WaitTimeout, wait_for,
                                      _refuse_to_block_the_loop)

        if until is None and given is None:
            act()
            return None
        _refuse_to_block_the_loop("a verb with given= or until=")
        deadline = time.monotonic() + timeout
        if until is None:
            self._hold(given, deadline, what)
            act()
            return None
        check = until if callable(until) else None
        if check is None and not getattr(until, "postcondition", True):
            raise ValueError(
                f"{type(until).__name__} cannot be a postcondition: it is "
                f"satisfied by the calm before the act as readily as the calm "
                f"after. Say the state you expect (Reads, Appears), or put it "
                f"in given= or a wait() of its own.")
        holds = check or (lambda: until.check(self))
        attempts = 0
        while True:
            if given is not None:
                self._hold(given, deadline, what)
            act()
            attempts += 1
            window = min(retry_interval, deadline - time.monotonic())
            try:
                return wait_for(holds, timeout=max(window, 0.0),
                                message=str(until))
            except WaitTimeout:
                pass
            if time.monotonic() >= deadline:
                raise WaitTimeout(
                    f"{what} did not take: waited {timeout:.0f}s for "
                    f"{until} over {attempts} attempts")

    def _hold(self, given, deadline: float, what: str):
        """Wait for a precondition, and fail as a precondition.

        Named apart from the postcondition on purpose: "the world was not
        ready" and "the act did not take" want different repairs, and a
        message that says which is the difference between a fix and a guess.

        lazydocs: ignore
        """
        from keyhac.core.wait import WaitTimeout, wait_for

        left = max(deadline - time.monotonic(), 0.0)
        try:
            if callable(given):
                return wait_for(given, timeout=left, message=str(given))
            return given.wait(self, left)
        except WaitTimeout:
            raise WaitTimeout(f"{what} never started: waited for {given}") from None

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
