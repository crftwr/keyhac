"""Balloon tooltips, drawn as screen marks.

Restores keyhac-win's popBalloon/closeBalloon (multi-stroke help, macro
status).  Placement: under the caret when one can be read and believed
(`keyhac.core.anchor`), and otherwise the top-right of the main screen's
work area - which is where a balloon with nothing to point at belongs.

**A balloon is a mark, not a window.**  It used to be a frameless topmost
non-activating window with a `Label` in it, which is five window-style fields
spelling out "a tooltip", and it could be clicked - which for a tooltip is
simply wrong.  A screen mark says the intent once, comes with click-through,
and lets the text wrap instead of being squeezed onto one line inside a
window sized by `min(70, len(text) + 4)`.
"""

from puikit import Style

from keyhac.core import log

logger = log.getLogger("Balloon")

#: Wrap width in base units.  The old window sized itself with
#: `min(70, max(14, len(text) + 4))`, which was this number with no name and
#: no way for a long balloon to do anything but be cut short.
_MAX_WIDTH_UNITS = 70

#: Distance from the corner of the work area.
_INSET_PX = 24

#: How far below a window's top edge the title-bar placement starts.  Flush
#: with the edge reads as part of the window's frame rather than as something
#: laid on top of it; a couple of pixels is enough to say "on the title bar"
#: without leaving the bar.
_TITLE_DROP_PX = 2

#: A tooltip reads as a note, not as a window: a warm fill, dark text, and a
#: soft corner.  `None` anywhere here means "the theme's", which a balloon
#: cannot ask for - it has no Panel and therefore no theme.
_STYLE = Style(fg=(28, 28, 30), bg=(250, 240, 170))
_RADIUS = 6.0


def multi_stroke_help(balloon: "BalloonManager", keymap):
    """The callback `main()` hands to `keymap.on_enter_multi_stroke`.

    A function rather than a lambda in the bootstrap because of what it
    reads, and that is a claim worth a test that a lambda inside `main()`
    cannot have.

    **Where the focus is now, not where the keystroke found it.**
    `keymap.focus` looked like the free answer - it is refreshed at the top
    of `_on_key_down`, so it belongs to the very key that armed the prefix,
    and reading it keeps a second focus lookup off the hook's clock. On
    macOS it is also *true*: that provider reads `AXFocusedUIElement` fresh
    every time. On Windows it is a cache keyed on the foreground window, the
    focused child window and the title, because a full UIA walk measured
    33 ms and cannot run on every key - and none of those three change when
    the focus moves *inside* a window. In a Chromium or Electron window they
    cannot: the whole UI is one HWND, so tabbing from the address bar into
    the page changes nothing the probe can see, and the balloon kept opening
    at the field the user had just left. Issue #44 is the same staleness,
    found in the action API, and `keymap.ui.focused()` stopped reading the
    snapshot for the same reason.

    So the provider is asked, at 2.1 ms measured against a cross-process
    Edit - once when a prefix is armed, not once per keystroke, against a
    300 ms hook deadline. The snapshot stays as the fall-back: asking can
    answer nothing where the snapshot still holds the window or the
    application, which is a place when the fresh read is not.

    It takes the focused *field* when there is no caret to be had, which is
    every Electron application: they answer `AXBoundsForRange` with
    CGRectZero and building their accessibility tree first does not change
    it. Under a one-line field is within a line of where the caret is.

    When even that is refused - a tall element, or none at all, which is what
    Excel reports with no cell being edited - it falls to the **top edge of
    the focused window**, centred: the title bar. That is a strip holding
    nothing the user is reading, it names the window the balloon is about,
    and it is on screen where the work is rather than in a corner of another
    monitor. The corner is left for having no window either.

    Args:
        balloon: the BalloonManager to pop on.
        keymap: the Keymap whose focus the caret is read from.

    Returns:
        A `callable(name)` for `on_enter_multi_stroke`.
    """
    def show(name):
        from keyhac.core.anchor import popup_anchor
        element = _focused_element_now(keymap)
        if element is None:
            element = getattr(keymap.focus, "element", None)
        found = popup_anchor(element, _focused_window_rect(keymap))
        text = f"Multi-stroke: {name or '...'}"
        if found is None:
            balloon.pop("MultiStroke", text)
        elif found[1] == "window":
            balloon.pop("MultiStroke", text, over=found[0])
        else:
            balloon.pop("MultiStroke", text, near=found[0])

    return show


def _focused_element_now(keymap):
    """The focused element as the platform reports it this instant, or None.

    The same question `keymap.ui.focused()` asks, and for the same reason -
    see this module's `multi_stroke_help`. None where the provider would
    rather say nothing than hand back a window or an application pretending
    to be the focus (issue #44), which is why the caller keeps the snapshot
    behind it, and None too when the read raises: a balloon that fails to
    open is worse than one in a corner.
    """
    provider = getattr(keymap, "_focus_provider", None)
    ask = getattr(provider, "get_focused_element", None)
    if ask is None:
        return None
    try:
        return ask()
    except Exception:
        logger.debug("Could not ask where the focus is; using the snapshot.")
        return None


def _focused_window_rect(keymap):
    """The focused window's frame, or None.

    On the key hook's clock, like the rest of this callback - and affordable
    for the same reason the chooser's own centring is: it asks the window
    provider the same question from inside the hook callback, and has since
    issue #4.
    """
    ask = getattr(keymap, "get_active_window", None)
    if ask is None:
        return None
    try:
        window = ask()
        return window.get_frame() if window is not None else None
    except Exception:
        return None


class BalloonManager:

    def __init__(self, backend):
        self._backend = backend
        self._balloons = {}  # name -> ScreenMarker

    def pop(self, name: str, text: str, timeout: float = None,
            near=None, over=None) -> None:
        """Show (or replace) a named balloon; timeout in seconds.

        Args:
            name: Which balloon this is; popping the same name replaces it.
            text: What it says. It wraps rather than being cut short.
            timeout: Seconds until it closes itself, or None to leave it.
            near: A screen rect to sit *under* - the caret, for the
                multi-stroke help that appears while the user is typing.
            over: A screen rect to sit centred on the *top edge* of - the
                focused window, for when there is nothing inside it to point
                at. `near` wins if both are given.
        """
        self.close(name)
        base_w, _base_h = self._backend.base_size
        max_width = _MAX_WIDTH_UNITS * base_w
        try:
            marker = self._backend.mark_screen(
                *self._place(max_width, text, near, over), text=text, fill=True,
                style=_STYLE, radius=_RADIUS, max_width=max_width,
                timeout=timeout,
                # A balloon appears in a corner nobody was watching.
                flash=True)
        except Exception:
            logger.debug("This platform cannot draw a balloon.")
            return
        self._balloons[name] = marker

    def close(self, name: str = None) -> None:
        """Close one balloon, or all when name is None."""
        names = [name] if name is not None else list(self._balloons)
        for one in names:
            marker = self._balloons.pop(one, None)
            if marker is not None:
                marker.close()

    def _place(self, max_width: float, text: str, near, over=None) -> tuple:
        """Under `near`, else on `over`'s top edge, else the corner.

        **Both are estimates**, and have to be: a screen mark sizes itself to
        its text and does not exist yet.  They decide only whether the balloon
        would run off an edge, so an estimate wrong by a line moves it by a
        line - unlike the window the chooser places, which knows its own frame
        before it is shown.

        **The width has to be the text's, not the wrap width.**  `_corner()`
        places at the widest the mark could be, which is right when it is
        being pushed against the right edge of the screen; here it is being
        put at a caret, and a short balloon measured as 70 columns wide is
        one that thinks it will not fit.  Measured with Terminal.app near the
        right edge of a 1710-wide screen: a caret at x=1406 and an eighteen
        character balloon, clamped to 1710 - 560 = 1150 - a quarter of the
        screen to the left of the caret it was meant to be under.
        """
        if near is None and over is None:
            corner = self._corner(max_width)
            logger.debug(f"Balloon in the corner {corner}: nothing to place "
                         f"it against, not even a window.")
            return corner
        from keyhac.core.anchor import place_below, place_over_top
        base_w, base_h = self._backend.base_size
        columns = min(_MAX_WIDTH_UNITS, len(text))
        lines = max(1, -(-len(text) // _MAX_WIDTH_UNITS))
        width = columns * base_w + _INSET_PX
        height = lines * base_h + _INSET_PX
        anchor = near if near is not None else over
        logger.debug(
            f"Balloon {'under' if near is not None else 'on the top edge of'} "
            f"{tuple(round(v) for v in anchor)}: "
            f"{len(text)} chars -> {columns} column(s) x {base_w} and "
            f"{lines} line(s) x {base_h}, + {_INSET_PX:.0f} -> estimated "
            f"{width:.0f}x{height:.0f} (wraps at {max_width:.0f})")
        if near is not None:
            return place_below((width, height), anchor, self._work_area(anchor))
        return place_over_top((width, height), anchor, self._work_area(anchor),
                              drop=_TITLE_DROP_PX)

    def _work_area(self, near) -> tuple | None:
        """The work area of the screen the anchor is on, or None.

        The screen the *caret* is on, not the main one: a balloon clamped to
        a screen the user is not looking at is a balloon nobody sees.
        """
        try:
            frames = self._backend.screen_frames()
        except Exception:
            return None
        if not frames:
            return None
        x, y = near[0], near[1]
        for index, (_full, work) in enumerate(frames):
            wx, wy, ww, wh = work
            if wx <= x < wx + ww and wy <= y < wy + wh:
                logger.debug(f"Balloon on screen {index} of {len(frames)}, "
                             f"work area {work}.")
                return work
        logger.debug(f"Balloon anchor ({x}, {y}) is on no screen's work area "
                     f"of {len(frames)}; clamping to the first.")
        return frames[0][1]

    def _corner(self, max_width: float) -> tuple:
        """Top-right of the main screen's work area.

        The mark sizes itself to its text, so the left edge is only known
        after it exists - and it is placed at the widest it could be instead,
        which keeps a short balloon a little further from the edge rather
        than letting a long one run off it.
        """
        try:
            frames = self._backend.screen_frames()
        except Exception:
            frames = None
        if not frames:
            logger.debug("Balloon placement unavailable; using the origin.")
            return (0, 0)
        _full, (vx, vy, vw, _vh) = frames[0]
        return (vx + vw - max_width - _INSET_PX, vy + _INSET_PX)
