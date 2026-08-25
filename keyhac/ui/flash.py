"""The focus flash - an outline that shows where the keyboard just went.

Moving focus with a key is not like clicking: a click tells you where focus
landed because you were looking there, while a keystroke teleports it and
leaves you to find it. That is the cost the flash is paying off, and it is why
the outline *travels* rather than simply appearing at the destination - the
journey says which way focus went, so an overshoot reads as an overshoot
rather than as focus vanishing.

Drawn on a transparent, click-through, non-activating overlay (puikit#124).
Each of those three matters and none is decorative:

    activates=False   showing it must not take the keyboard back off the pane
                      the action just gave it to.
    click_through     the overlay covers what it points at; hit-tested, it
                      would swallow the click aimed at the thing it marks.
    transparent       an opaque window can only *cover* what it marks, so the
                      thing you wanted to look at ends up behind the thing
                      telling you to look at it.

FIRE AND FORGET. `show()` is called from a ThreadedAction worker and returns
at once; every frame runs on the event-loop thread, which is also the thread
servicing the keyboard hook. An animation that delayed the movement would be a
worse problem than the one it solves.
"""

import time

from puikit import Panel, Style, WindowStyle
from puikit.widgets import Widget

from keyhac.core import log

logger = log.getLogger("Flash")

#: Everything the overlay is, in one place - see the module docstring.
FLASH_STYLE = WindowStyle(frameless=True, topmost=True, activates=False,
                          resizable=False, tool=True,
                          click_through=True, transparent=True)

#: The outline's colour, its stroke in device pixels, and its corner radius.
ACCENT = (41, 140, 255)
LINE_WIDTH = 4
RADIUS = 8.0

#: How faintly the interior is washed, at full opacity (0-255).
FILL_ALPHA = 34

#: The two halves of the flash, in seconds.
#:
#: The outline is invisible where focus *was*, reaches full strength exactly as
#: it arrives where focus *is*, and then fades where it landed.  So the
#: brightest moment is the answer to the question being asked - "where did it
#: go" - while the journey that explains it stays faint enough not to drag the
#: eye back to a pane already left behind.  Fading *while* travelling, which is
#: what this did first, put the emphasis on the departure instead.
TRAVEL = 0.15
FADE = 0.05

#: The whole flash.  Long enough to follow, short enough not to be in the way
#: of the next keystroke.
DURATION = TRAVEL + FADE

#: Frame interval.  A timer chain rather than request_animation_ticks, so this
#: needs no animation capability and behaves the same on every backend.
FRAME = 1 / 60.0

#: Slack around the travelled area, so the stroke is not clipped by its own
#: window at either end.
PADDING = 40.0


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _lerp(a, b, t):
    return tuple(u + (v - u) * t for u, v in zip(a, b))


class _Outline(Widget):
    """One rounded rectangle, placed in screen pixels inside the overlay."""

    def __init__(self, origin_px, base_size):
        self._origin = origin_px
        self._base = base_size
        self.rect = None
        self.alpha = 1.0

    def draw(self, ctx):
        if self.rect is None:
            return
        ox, oy = self._origin
        bw, bh = self._base
        x, y, w, h = self.rect
        a = max(0.0, min(1.0, self.alpha))
        ctx.round_rect(
            (x - ox) / bw, (y - oy) / bh, w / bw, h / bh,
            Style(fg=(*ACCENT, int(255 * a)), bg=(*ACCENT, int(FILL_ALPHA * a))),
            radius=RADIUS, hints={"fill": True, "line_width": LINE_WIDTH},
        )


class FlashManager:
    """Owns the one overlay at a time.  Built by the UI runtime."""

    def __init__(self, backend):
        self._backend = backend
        self._window = None
        self._cancel = None

    def show(self, source_rect, dest_rect) -> None:
        """Travel an outline from `source_rect` to `dest_rect`.

        UI-thread only; `keyhac.ui.flash.flash()` is what an action calls.
        """
        self._dismiss()
        if not dest_rect:
            return
        source_rect = source_rect or dest_rect

        bw, bh = self._backend.base_size
        x0 = min(source_rect[0], dest_rect[0]) - PADDING
        y0 = min(source_rect[1], dest_rect[1]) - PADDING
        x1 = max(source_rect[0] + source_rect[2], dest_rect[0] + dest_rect[2]) + PADDING
        y1 = max(source_rect[1] + source_rect[3], dest_rect[1] + dest_rect[3]) + PADDING
        cols = max(1, round((x1 - x0) / bw))
        rows = max(1, round((y1 - y0) / bh))

        window = self._backend.create_window(cols, rows, style=FLASH_STYLE)
        window.move_to_px(x0, y0)
        outline = _Outline((x0, y0), (bw, bh))
        panel = Panel(self._backend, window=window)
        panel.add(outline, x=0, y=0, w=cols, h=rows)
        self._window = window

        start = time.monotonic()

        def frame():
            if self._window is not window:
                return                      # a newer flash took over
            elapsed = time.monotonic() - start
            if elapsed < TRAVEL:
                # Travelling: opacity is tied to position, so "transparent at
                # the source, solid at the target" is one fact rather than two
                # curves that have to be kept in step.
                progress = _ease_out(elapsed / TRAVEL)
                outline.rect = _lerp(source_rect, dest_rect, progress)
                outline.alpha = progress
            elif elapsed < DURATION:
                # Landed: 1 - t^2 leaves full strength at zero slope, so the
                # arrival still registers before it goes.
                t = (elapsed - TRAVEL) / FADE
                outline.rect = dest_rect
                outline.alpha = 1.0 - t * t
            else:
                self._dismiss()
                return
            panel.render()
            self._cancel = self._backend.call_later(FRAME, frame)

        outline.rect = source_rect
        outline.alpha = 0.0             # invisible where focus was
        panel.render()
        window.show()
        self._cancel = self._backend.call_later(FRAME, frame)

    def _dismiss(self) -> None:
        if self._cancel is not None:
            try:
                self._cancel()
            except Exception:               # noqa: BLE001 - already fired
                pass
            self._cancel = None
        if self._window is not None:
            try:
                self._window.hide()
                self._window.close()
            except Exception:               # noqa: BLE001 - already gone
                logger.debug("could not close the flash overlay", exc_info=True)
            self._window = None


def flash(source_rect, dest_rect) -> None:
    """Show the flash, from any thread, without waiting for it.

    A no-op when running headless (--no-ui), and when anything about it fails:
    the flash is feedback about a move that has already happened, so it must
    never turn a working binding into a broken one.
    """
    from keyhac.core.keymap import Keymap
    from keyhac.ui import runtime

    if runtime.backend is None:
        return
    keymap = Keymap.get_instance()
    if keymap is None:
        return

    def run():
        try:
            if runtime.flash_manager is None:
                runtime.flash_manager = FlashManager(runtime.backend)
            runtime.flash_manager.show(source_rect, dest_rect)
        except Exception:                   # noqa: BLE001 - see the docstring
            logger.debug("focus flash failed", exc_info=True)

    keymap.call_on_main_thread(run)
