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

#: A tooltip reads as a note, not as a window: a warm fill, dark text, and a
#: soft corner.  `None` anywhere here means "the theme's", which a balloon
#: cannot ask for - it has no Panel and therefore no theme.
_STYLE = Style(fg=(28, 28, 30), bg=(250, 240, 170))
_RADIUS = 6.0


def multi_stroke_help(balloon: "BalloonManager", keymap):
    """The callback `main()` hands to `keymap.on_enter_multi_stroke`.

    A function rather than a lambda in the bootstrap because of what it
    reads: `keymap.focus` is the focus snapshot the *current* keystroke was
    dispatched against - refreshed at the top of `_on_key_down`, so it is the
    caret of the key that armed the prefix - and taking it from there is what
    keeps a second focus lookup off the hook's clock. That is a claim worth a
    test, and a lambda inside `main()` cannot have one.

    Args:
        balloon: the BalloonManager to pop on.
        keymap: the Keymap whose focus the caret is read from.

    Returns:
        A `callable(name)` for `on_enter_multi_stroke`.
    """
    def show(name):
        from keyhac.core.anchor import caret_anchor
        focus = keymap.focus
        balloon.pop("MultiStroke", f"Multi-stroke: {name or '...'}",
                    near=caret_anchor(getattr(focus, "element", None)))

    return show


class BalloonManager:

    def __init__(self, backend):
        self._backend = backend
        self._balloons = {}  # name -> ScreenMarker

    def pop(self, name: str, text: str, timeout: float = None,
            near=None) -> None:
        """Show (or replace) a named balloon; timeout in seconds.

        `near` is a screen rect to sit under - the caret, for the multi-stroke
        help that appears while the user is typing into something.
        """
        self.close(name)
        base_w, _base_h = self._backend.base_size
        max_width = _MAX_WIDTH_UNITS * base_w
        try:
            marker = self._backend.mark_screen(
                *self._place(max_width, text, near), text=text, fill=True,
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

    def _place(self, max_width: float, text: str, near) -> tuple:
        """Under `near` when there is one, and the corner when there is not.

        **The height is an estimate**, and has to be: a screen mark sizes
        itself to its text and does not exist yet.  It is used only to decide
        whether the balloon would run off the bottom, so an estimate that is
        wrong by a line moves the balloon by a line - unlike the window the
        chooser places, which knows its own frame before it is shown.
        """
        if near is None:
            logger.debug("Balloon in the corner: nothing to place it against.")
            return self._corner(max_width)
        from keyhac.core.anchor import place_below
        logger.debug(f"Balloon under {near}.")
        _base_w, base_h = self._backend.base_size
        lines = max(1, -(-len(text) // _MAX_WIDTH_UNITS))
        return place_below((max_width, lines * base_h + _INSET_PX),
                           near, self._work_area(near))

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
        for _full, work in frames:
            wx, wy, ww, wh = work
            if wx <= x < wx + ww and wy <= y < wy + wh:
                return work
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
