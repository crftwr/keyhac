"""Writing into the UI: text into fields, states into checkboxes.

The read side of an action is a search; the write side is three mechanisms that
are not interchangeable, and choosing wrong fails in ways that look like
success (doc/dev/ai-integration.md §7.3).

    paste      clipboard + Cmd/Ctrl-V.  Fast, bypasses the IME, fires the
               input events a web framework listens for.  Costs the clipboard,
               which is why preserve_clipboard() exists, and some fields
               refuse paste outright.
    keys       key injection.  Works everywhere and produces the most faithful
               event stream, but it is slow and it goes through the IME - with
               a CJK input source active, typed Latin text can arrive composed.
    set_value  AXValue / the UIA Value pattern.  Instant and IME-independent,
               and the one with the worst failure mode: React and Vue commonly
               do not observe it, so the value appears on screen while the
               framework's state stays empty and the form submits blank.

Measured on macOS (Safari, focused field): set_value ~5 ms, keys ~70 ms, paste
~105 ms - all three work.  The order here is still **paste, then keys**, with
set_value opt-in only, because speed is not the axis that matters: the one that
fails on a React form fails *invisibly*, and it is the fastest of the three.

Focus first, always.  An unfocused write is silently ignored - which is how an
earlier measurement concluded that set_value does nothing at all - and
unfocused keystrokes are worse, because they land in whatever does have focus.

WHAT MAKES IT SAFE.  Every write is read back, and a write that cannot be read
back is a failure rather than a warning.  That single rule is what turns all
three mechanisms' silent failures into loud ones, and it is why set_text()
falls back rather than hoping.

Which is also why verify=False is more expensive than it looks: the read-back
is not only the proof that the write landed, it is the *only* signal that the
target has finished reading the pasteboard, and the clipboard cannot be put
back before it has.  Turning it off used to leave paste racing the restore, and
the race is not close - see PASTE_SETTLE.

THREADS.  Focusing and reading elements is main-thread work; sending keys is
not.  These are written to be called from ThreadedAction.run(), and dispatch
the element half themselves.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any, Iterable

from keyhac.core import log
from keyhac.core.uitree import StaleElement, UINode
from keyhac.core.wait import (WaitTimeout, evaluate_on_main_thread,
                              on_loop_thread, wait_for)

logger = log.getLogger("Fill")

#: Mechanisms in the order set_text() tries them.  set_value is not here on
#: purpose; pass methods=("set_value",) to ask for it explicitly.
DEFAULT_METHODS = ("paste", "keys")

#: How long to wait for a written value to show up when reading it back.  Not
#: zero: a paste is delivered as a keystroke and the application processes it
#: on its own schedule.
VERIFY_TIMEOUT = 2.0

#: How long to keep asking for the focus before giving up on it.
#:
#: Not zero, for the same reason VERIFY_TIMEOUT is not: landing is not
#: instant.  macOS answers "did it land" against the system-wide focused
#: element read back immediately after the write, and a document that has just
#: come to the front accepts the focus a beat later than that - measured at
#: 121 ms for an accessibility answer to catch up in Safari, so an instant
#: check reports a false "could not focus" for a focus that is on its way.
#: The ask is repeated rather than only re-read: an application that was not
#: ready to take the focus needs asking again, not watching.  Safe to repeat -
#: focusing is idempotent, which is what separates it from the acts that must
#: not be retried.  Too generous only makes a genuine failure slower.
FOCUS_TIMEOUT = 1.0

#: How long an unverified paste holds the clipboard before restoring it.
#:
#: Only reached with verify=False, where there is nothing to wait *for*: the
#: read-back is what normally tells us the target has taken the pasteboard.
#: Without it the restore went out in the same breath as Ctrl-V and the field
#: received the *previous* clipboard contents - observed live on Windows 11
#: (Notepad, tools/uia_pass.py), where a field ended up holding the shell
#: command the operator had copied an hour earlier.  A fixed delay is a guess
#: and says so; it is the price of asking for a write nobody can confirm.
PASTE_SETTLE = 0.5


class FillFailed(RuntimeError):
    """A write did not take.

    Carries what was attempted, because "the field is still empty" and "the
    field has the wrong text" want different responses from the caller.

    **An empty `attempted` means nothing was written at all** - the focus
    never landed, so the write was refused rather than attempted.  That one is
    a precondition failure wearing this exception's name: the world was not
    ready, as against the act not having taken, and only the second carries
    the double-act hazard that makes retrying dangerous.
    """

    def __init__(self, message: str, attempted: Iterable[str] = ()):
        super().__init__(message)
        self.attempted = tuple(attempted)


def _element(target):
    """Accept a UINode or a platform element, return the platform element."""
    return target.element if isinstance(target, UINode) else target


def _keymap():
    from keyhac.core.keymap import Keymap
    return Keymap.get_instance()


# -- clipboard ---------------------------------------------------------------

#: Held across the whole save/write/restore, because that sequence is the one
#: shared resource the action pool's single worker used to protect for free.
#: With more than one worker, two actions pasting at once would each save the
#: other's scratch value and put it back as "what the user had".
#:
#: Reentrant, and not optionally: `_paste` opens this context itself, while
#: the documented way to write several fields is to wrap the lot in one - so
#: the nested case is the normal case, and a plain Lock would deadlock the
#: example in the docstring below.
_clipboard_lock = threading.RLock()


@contextlib.contextmanager
def preserve_clipboard():
    """Put the clipboard back the way it was.

    Pasting is the default write mechanism, and an action that silently
    replaces what the user had copied is an action they stop trusting.  A
    clipboard holding something that is not text (an image, a file promise)
    cannot be saved this way - the restore puts back the text that was there,
    or nothing.

    ```python
    with preserve_clipboard():
        set_text(field, "REC-001")
    ```
    """
    with _clipboard_lock:
        keymap = _keymap()
        clipboard = keymap.clipboard if keymap else None
        saved = None
        if clipboard is not None:
            try:
                saved = clipboard.get_text()
            except Exception:
                logger.debug("could not read the clipboard to save it",
                             exc_info=True)
        try:
            yield
        finally:
            if clipboard is not None and saved is not None:
                try:
                    clipboard.set_text(saved)
                except Exception:
                    logger.debug("could not restore the clipboard",
                                 exc_info=True)


# -- focus -------------------------------------------------------------------

def focus(target, timeout: float = None) -> bool:
    """Give an element keyboard focus, and report whether it landed.

    Checked rather than assumed: both platforms accept the request without
    always honouring it, and a keystroke aimed at an element that never got
    focus goes wherever focus actually is - which is how a form fill ends up
    typing its data into the page behind it.

    **Asked until it lands, not asked once.** The check used to happen in the
    same breath as the write, which is the layer's own "asking is not the same
    as it being so" in the one place that had kept it: a field in a document
    that had just been brought to the front reported a focus failure for a
    focus that arrived milliseconds later.  See FOCUS_TIMEOUT.

    **Landed means on this element**, not merely inside it.  Everything that
    contains the focused control also contains the focus - the group around a
    field, the document, the window - and a keystroke sent on that answer goes
    to whichever control inside actually has it.  A control that hands its
    focus to a part of itself (a Windows combo box focuses its edit part) is
    therefore False here: aim at the part, which is in the tree with an
    identifier of its own.  `contains_focus()` answers the other question for
    a caller that wants it.

    Args:
        target: A UINode or platform element.
        timeout: Seconds to keep asking, FOCUS_TIMEOUT by default.  Zero asks
            once, which is also what happens on the event-loop thread, where
            waiting is refused.

    Returns:
        True when the focus landed, False when it never did.
    """
    element = _element(target)
    timeout = FOCUS_TIMEOUT if timeout is None else timeout

    def ask():
        return _ask_for_focus(element)

    if bool(evaluate_on_main_thread(ask)):
        return True
    # Never RuntimeError where this used to answer: reading and asking are
    # both fine on the loop thread, and only the waiting is not.
    if timeout <= 0 or on_loop_thread():
        return False
    try:
        return bool(wait_for(lambda: evaluate_on_main_thread(ask),
                             timeout=timeout,
                             message="the focus to land on the element"))
    except WaitTimeout:
        return False


def _ask_for_focus(element) -> bool:
    """Ask for the focus, then find out whether it landed on this element.

    One function because the two halves are not independent: an element of the
    pre-split shape answers the question *with* the act, and only the caller
    that made the act can read that answer.
    """
    setter = getattr(element, "set_focus", None)
    if setter is None:
        return False
    answer = setter()
    # The pre-split shape: set_focus() returned the verdict itself.  Kept for
    # duck-typed elements that predate the two predicates, the way
    # _raise_if_stale keeps a path for elements that predate is_stale.
    if answer is not None:
        return bool(answer)
    strict = getattr(element, "has_focus", None)
    return bool(strict()) if strict is not None else False


def _where_the_focus_went(element) -> str:
    """A sentence for the caller who named the wrong element, or "".

    `contains_focus()` is read here for diagnosis and nowhere for permission.
    Deciding *which* controls may write on an inside-answer would mean core
    holding a list of platform role names - the combo boxes, the spin boxes,
    and whatever the next framework calls them - and inventing a portable
    vocabulary for platform data is the thing this layer has always refused to
    do.  Naming what actually took the focus costs one read and tells the
    author what to aim at instead, which is the part of the list that was
    worth having.

    The element is named only when a keymap is wired, since that is where the
    focus provider lives; a bare library call still gets the sentence, without
    the name.  Runs on the main thread, like every other element read here.
    """

    def look() -> str:
        inside = getattr(element, "contains_focus", None)
        if inside is None or not inside():
            return ""
        describe = getattr(_focused_element(), "describe", None)
        described = describe() if describe is not None else {}
        role, identifier = described.get("role"), described.get("identifier")
        where = str(role) if role else "something"
        if identifier:
            where += f" (identifier {identifier!r})"
        # Deliberately not saying *why* the focus is inside: a control that
        # delegates to a part of itself and a container that merely holds the
        # focused control look the same from here, and telling them apart is
        # the caller's business. Naming what took the focus serves both.
        return (f"; the focus landed inside it, on {where} - write to that "
                f"element if it is the one you meant")

    try:
        return str(evaluate_on_main_thread(look))
    except Exception:      # a diagnosis must never replace the real failure
        return ""


def _focused_element():
    """Whatever holds the keyboard focus now, or None.

    The same door `UI.focused` uses - get_focused_element(), not get_focus(),
    since the frozen hook-time answer is not what an action is asking.
    """
    keymap = _keymap()
    provider = getattr(keymap, "_focus_provider", None) if keymap else None
    getter = getattr(provider, "get_focused_element", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


def read_value(target) -> Any:
    """The element's current content, from the loop thread."""
    element = _element(target)

    def read():
        describe = getattr(element, "describe", None)
        if describe is not None:
            return describe().get("value")
        return None

    return evaluate_on_main_thread(read)


# -- text --------------------------------------------------------------------

def set_text(target, text: str, methods: Iterable[str] = DEFAULT_METHODS,
             clear: bool = True, verify: bool = True,
             timeout: float = VERIFY_TIMEOUT) -> str:
    """Put `text` into a field, and prove that it arrived.

    Args:
        target: A UINode or platform element - the field itself, not its label.
        text: What to write.
        methods: Mechanisms to try in order; the first whose value reads back
            correctly wins.  "paste", "keys", "set_value".
        clear: Select the existing content first, so the write replaces rather
            than appends.
        verify: Read the value back and require it to match.  Turning this off
            is how silent failures get shipped; it exists only for fields whose
            value genuinely cannot be read (a password field).  A paste then
            has to fall back on PASTE_SETTLE, since the read-back is also what
            tells us the clipboard is safe to restore.
        timeout: How long to wait for the value to appear.

    Returns:
        The method that worked.

    Raises:
        FillFailed: No mechanism produced the text.
    """
    element = _element(target)
    if not focus(target):
        raise FillFailed(
            f"could not focus the field, after {FOCUS_TIMEOUT:g}s of asking; a "
            f"write now would go to whatever has focus instead"
            + _where_the_focus_went(element), attempted=())

    def confirm() -> bool:
        if not verify:
            return True
        try:
            wait_for(lambda: _matches(element, text), timeout=timeout,
                     message=f"the field to read back as {text!r}")
            return True
        except WaitTimeout:
            return False

    tried = []
    reasons = []
    for method in methods:
        tried.append(method)
        try:
            if method == "paste":
                # Verification happens *inside* the clipboard swap.  Restoring
                # as soon as the keystroke is posted races the application's
                # read of the pasteboard, and the race is not close: the field
                # came back holding whatever had been on the clipboard before,
                # which is a wrong value that looks like a successful paste.
                if _paste(text, clear, confirm, settle=not verify):
                    return method
                reasons.append(f"paste: wrote nothing readable "
                               f"(field reads {read_value(target)!r})")
                continue
            _write(element, text, method, clear)
        except Exception as error:
            # Why it failed, not just that it did.  An early version swallowed
            # these, and a setup error - key names not initialised, so every
            # send_key raised - looked exactly like "paste does not work on
            # this field", which sent the investigation in the wrong direction
            # entirely.
            reasons.append(f"{method}: {type(error).__name__}: {error}")
            logger.debug(f"{method} write raised: {error}", exc_info=True)
            continue
        if confirm():
            return method
        got = read_value(target)
        reasons.append(f"{method}: wrote nothing readable (field reads {got!r})")
        logger.debug(f"{method} did not take: field reads {got!r}")

    raise FillFailed(
        f"could not write {text!r} into the field - "
        + "; ".join(reasons), attempted=tried)


def _matches(element, text: str) -> bool:
    describe = getattr(element, "describe", None)
    value = describe().get("value") if describe is not None else None
    return value == text


def _write(element, text: str, method: str, clear: bool) -> None:
    if method == "set_value":
        evaluate_on_main_thread(lambda: element.set_value(text))
        return
    if method == "keys":
        _type(text, clear)
        return
    raise ValueError(f"unknown write method {method!r} "
                     f"(known: paste, keys, set_value)")


def _select_all(ctx) -> None:
    keymap = _keymap()
    ctx.send_key("Cmd-A" if keymap and keymap.platform == "mac" else "Ctrl-A")


def _paste(text: str, clear: bool, confirm, settle: bool = False) -> bool:
    """Paste `text`, and hold the clipboard swapped until `confirm()` answers.

    The clipboard cannot go back until the target application has actually
    read it, and the only signal that it has is the value arriving in the
    field - so the verification runs inside this context, not after it.

    `settle` is that signal's absence: with verify=False there is no read-back
    to wait on, so the hold becomes a fixed PASTE_SETTLE rather than nothing at
    all.  It is a weaker guarantee than confirm() and the caller is told so.
    """
    keymap = _keymap()
    clipboard = keymap.clipboard if keymap else None
    if clipboard is None:
        raise RuntimeError("no clipboard available for the paste method")
    with preserve_clipboard():
        clipboard.set_text(text)
        with keymap.get_input_context() as ctx:
            if clear:
                _select_all(ctx)
            ctx.send_key("Cmd-V" if keymap.platform == "mac" else "Ctrl-V")
        if settle:
            logger.warning(
                f"pasting without verification: holding the clipboard "
                f"{PASTE_SETTLE}s and hoping, since nothing can confirm the "
                f"target read it. Prefer methods=(\"keys\",) for a field whose "
                f"value cannot be read back.")
            time.sleep(PASTE_SETTLE)
        return bool(confirm())


def _type(text: str, clear: bool) -> None:
    keymap = _keymap()
    if keymap is None:
        raise RuntimeError("no keymap available for the keys method")
    with keymap.get_input_context() as ctx:
        if clear:
            _select_all(ctx)
        ctx.send_text(text)


# -- other field types -------------------------------------------------------

def set_checked(target, checked: bool) -> bool:
    """Set a checkbox, reading it before deciding whether to press it.

    A checkbox pressed blindly *toggles*, so "tick this box" applied twice
    unticks it, and an action that is rerun after a partial failure undoes its
    own work.  Returns whether anything was pressed.

    Values are compared loosely because platforms disagree about the
    vocabulary: macOS AXValue is 0/1, the UIA ToggleState is 0/1/2, and
    "indeterminate" counts as not-checked.
    """
    element = _element(target)
    if _is_checked(element) == bool(checked):
        return False
    evaluate_on_main_thread(lambda: _press(element))
    try:
        wait_for(lambda: _is_checked(element) == bool(checked),
                 timeout=VERIFY_TIMEOUT,
                 message=f"the checkbox to read back as {checked}")
    except WaitTimeout:
        raise FillFailed(
            f"pressed the checkbox but it still reads "
            f"{read_value(target)!r}, wanted {checked}", attempted=("press",))
    return True


def _is_checked(element) -> bool:
    def read():
        describe = getattr(element, "describe", None)
        value = describe().get("value") if describe is not None else None
        if value is None:
            # Windows reports the toggle state as an attribute, not a value.
            getter = getattr(element, "get_attribute_value", None)
            value = getter("ToggleState") if getter else None
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "checked")
        return bool(value) and value != 2      # 2 is UIA's indeterminate
    return bool(evaluate_on_main_thread(read))


def _press(element) -> None:
    """Press an element with whichever action name the platform uses.

    "Select" is last and is not interchangeable with the rest: it is the only
    thing a Windows tab, list item or radio button answers to - such an element
    supports no Invoke and no Toggle at all - while on macOS the same control
    is pressed.  Ordering it after Invoke keeps a control that offers both
    behaving the way it did before.
    """
    names = element.get_action_names() or []
    for name in ("AXPress", "Invoke", "Toggle", "AXConfirm", "Select"):
        if name in names:
            if element.perform_action(name) is False:
                _raise_if_stale(element, f"{name} was refused")
                raise FillFailed(f"the element refused {name}")
            return
    # A dead element reports no actions, so this is where the two arrive at the
    # same place - and they need different answers. Ask before blaming the
    # selector: "supports no press action" sent an operator looking at their
    # action when the dialog had simply closed underneath it.
    _raise_if_stale(element, "it went away before it could be pressed")
    raise FillFailed(f"element supports no press action (has {names})")


def _raise_if_stale(element, what: str) -> None:
    """Turn the platform's "this element is gone" into the typed error.

    Policy lives here rather than in the platform layer, which only answers
    the question. `getattr` because a key binding and the tests both accept
    duck-typed elements that predate `is_stale`.
    """
    probe = getattr(element, "is_stale", None)
    if probe is not None and probe():
        raise StaleElement(f"the element is no longer on screen: {what}")


def press(target) -> None:
    """Press a button, link or menu item, portably.

    macOS calls it AXPress and Windows calls it Invoke; a widget that supports
    neither (a checkbox on Windows offers Toggle) is handled here rather than
    in every action.
    """
    element = _element(target)
    evaluate_on_main_thread(lambda: _press(element))
