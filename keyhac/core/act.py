"""Acting on an element, portably - the ladder every caller shares.

Split out of `keyhac.core.sources` when the chooser and the action API turned
out to want the same thing: **an accessibility press is accepted by
applications that then do nothing with it**, so "press it" is not one call, it
is an order of attempts ending in one that cannot be inert.

The order is a click where the screen can prove the control is at the point
about to be clicked, then the platform's press, then the focus. Only the first
is immune to being accepted and ignored, which is why it is first; only the
last two reach something that is not on screen, which is why they are there at
all.

`keyhac.core.fill` has a `press` of its own with different manners - it raises
`FillFailed`, and tells a dead element from an unpressable one, which is what
an action wants from a single named step. This is the ladder underneath.
"""

import time

from keyhac.core import log
from keyhac.core.keymap import Keymap

logger = log.getLogger("Act")


#: How far up from the element the screen reports at a point to look for the
#: one being pressed.  The point tested is the middle of the control, so what
#: is at it is usually the control's own label rather than the control - two
#: levels in VS Code, a text node inside a group inside the tab.  Ten leaves
#: room for a deeper skin without letting a miss walk all the way up to the
#: window.
_HIT_TEST_DEPTH = 10


class Acted:
    """What acting on an element did, and what stopped it doing more.

    `how` is "clicked", "pressed", "focused" or "" for nothing. `blocked` says
    why the click - the one attempt that cannot be inert - was not available:
    "covered" (something else is at the point, which is worth telling a person
    about), "unknown" (this platform cannot say what is at a point),
    "offscreen" (no usable rectangle), or "" when the click was simply taken.
    """

    __slots__ = ("how", "blocked")

    def __init__(self, how: str = "", blocked: str = ""):
        self.how = how
        self.blocked = blocked

    def __bool__(self):
        return bool(self.how)

    def __repr__(self):
        return f"Acted({self.how!r}, blocked={self.blocked!r})"


def act_on(element) -> Acted:
    """Do to `element` what choosing it means, by whichever way works.

    The whole ladder in one call, which is what keeps a caller from writing
    one of its own: the click first, the platform press behind it, the focus
    last (inside `press`).
    """
    outcome = click(element)
    if outcome.how:
        return outcome
    if press(element):
        return Acted("pressed", outcome.blocked)
    return Acted("", outcome.blocked)


def click(element) -> Acted:
    """Click `element` where it is on the screen, if the screen agrees that
    is where it is.

    **What makes it safe is the hit test, not the rectangle.** The OS is asked
    what is at the point about to be clicked, and the click only goes out when
    the answer is that element or something inside it - usually something
    inside it, since the middle of a control is its label. That rules out the
    stale rectangle, the window that moved, the control scrolled out of sight
    and the popover on top, each of which would otherwise click whatever is
    there instead, which is worse than not clicking at all.

    The pointer goes back where the user left it: parked on the control it
    would raise a tooltip and change what their next click means.
    """
    keymap = Keymap.get_instance()
    if keymap is None:
        return Acted("", "unknown")
    rect = _rect_of(element)
    if rect is None or rect[2] <= 0 or rect[3] <= 0:
        return Acted("", "offscreen")
    point = _centre(rect)
    at = _is_at(element, point)
    if at is False:
        # The one refusal the application itself can fix.
        moved = _scrolled_into_view(element, rect)
        if moved is not None and moved[2] > 0 and moved[3] > 0:
            rect, point = moved, _centre(moved)
            at = _is_at(element, point)
    if at is not True:
        if at is None:
            # Windows does not verify its ElementFromPoint yet.
            logger.debug("This platform cannot say what is at a point.")
            return Acted("", "unknown")
        logger.debug(f"Not clicking {point}: something else is there.")
        return Acted("", "covered")
    origin = keymap.cursor_pos()
    if origin is None:
        return Acted("", "unknown")
    try:
        with keymap.get_input_context() as ctx:
            ctx.send_mouse_move(point[0] - origin[0], point[1] - origin[1])
            ctx.send_mouse_button("left")
            ctx.send_mouse_move(origin[0] - point[0], origin[1] - point[1])
    except Exception:
        logger.debug("The control could not be clicked.", exc_info=True)
        return Acted("", "unknown")
    logger.debug(f"Clicked the control at {point}.")
    return Acted("clicked")


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


def press(element) -> bool:
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


def _identity_of(element):
    key = getattr(element, "identity_key", None)
    if key is None:
        return None
    try:
        return key()
    except Exception:
        return None
