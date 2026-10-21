"""The chooser's preview window: what the selected row holds, beside the list.

A row is one line, and some rows are not: an image, a long text, a list of
files, an action whose summary line does not say what it does.  The preview
shows those without changing the list - a separate window next to the
chooser, rather than a pane inside it, so the chooser's width and row height
stay what they are with or without it (issue #262).

**Display only.**  The window takes neither the keyboard nor clicks: the
chooser itself does not take OS focus (see ``keyhac.ui.chooser``), and a
second window that did would undo that.

Content comes from `CandidateSource.preview()` as a `CandidatePreview`.  An
image arrives encoded and is decoded here with Pillow - a puikit dependency
already, and Keyhac's own since it imports it - into a `RasterImage` scaled
to the window, off the main thread: a screenshot's PNG takes a tenth of a
second to decode, which on the main thread is a tenth of a second of keyboard.
"""

import io

from puikit import Panel, Style, TextAttribute, WindowStyle
from puikit.layout import HSplit, Item, VSplit
from puikit.widgets import Label

from keyhac.core import log
from keyhac.ui.frame import Frame

logger = log.getLogger("Chooser")

#: The window's width, in base units.  About half a default chooser: wide
#: enough for a paragraph, narrow enough to sit beside the chooser on a
#: laptop screen.
_WIDTH_UNITS = 44

#: Space between the chooser and the preview.
_GAP_PX = 6

_MARGIN_PX = 8

#: A field's label is context, its value content - the same split the
#: chooser's badge makes.
_FIELD_LABEL_STYLE = Style(fg=(130, 130, 140))
_TITLE_STYLE = Style(attr=TextAttribute.BOLD)

#: Decoded at up to twice the window's size in points, so a Retina screen
#: gets its pixels and a 5K screenshot does not sit in memory at full size.
_IMAGE_SCALE = 2


def decode_image(source, max_size: tuple):
    """`source` (encoded bytes, or a path) as a RasterImage no larger than
    `max_size` pixels, or None if it cannot be read.

    Safe on a worker thread: Pillow only, nothing of the UI's."""
    try:
        from PIL import Image
        from puikit.image import RasterImage

        image = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray))
                           else source)
        image.thumbnail((max(1, int(max_size[0])), max(1, int(max_size[1]))))
        return RasterImage.from_pillow(image.convert("RGBA"))
    except Exception:
        logger.debug("The preview image could not be decoded.", exc_info=True)
        return None


class PreviewWindow:
    """One display-only window, re-filled for each row it previews."""

    def __init__(self, backend):
        self._backend = backend
        self._window = None
        self._panel = None

    @property
    def is_open(self) -> bool:
        """Whether the window is on screen."""
        return self._window is not None

    def image_box(self, beside) -> tuple:
        """The pixel size an image is decoded to, for a preview beside the
        chooser frame `beside`."""
        width, height = self._size(beside)
        inner_w = width - 2 * _MARGIN_PX
        inner_h = height * 0.6
        return (inner_w * _IMAGE_SCALE, inner_h * _IMAGE_SCALE)

    def show(self, preview, raster, beside) -> None:
        """Show `preview` (with its image decoded to `raster`, or None) next
        to the chooser frame `beside` - `(x, y, w, h)` in pixels."""
        from puikit.image import CONTAIN
        from puikit.widgets.image import ImageView
        from puikit.widgets.text_block import TextBlock

        rows = []
        if preview.title:
            rows.append(Item(Label(preview.title, style=_TITLE_STYLE), size="content"))
        if raster is not None:
            rows.append(Item(ImageView(raster, fit=CONTAIN), weight=3))
        if preview.text:
            rows.append(Item(TextBlock(preview.text, wrap="word"), weight=2))
        elif raster is None:
            # Fields alone still need something to sit under, or they float
            # to the middle of an empty window.
            rows.append(Item(Label(""), weight=1))
        for label, value in preview.fields:
            rows.append(Item(HSplit(
                Item(Label(str(label), style=_FIELD_LABEL_STYLE), size="content"),
                Item(Label(str(value)), weight=1),
                gap=1), size="content"))
        if not rows:
            self.close()
            return

        x, y, w, h = self._place(beside)
        if self._window is None:
            base_w, base_h = self._backend.base_size
            self._window = self._backend.create_window(
                max(1, round(w / base_w)), max(1, round(h / base_h)),
                title="Keyhac Preview",
                # Display-only: the chooser keeps every input there is.
                style=WindowStyle(topmost=True, resizable=False, tool=True,
                                  activates=False, movable=False,
                                  frameless=True, overlay_input="none"))
            self._panel = Panel(self._backend, window=self._window)
        self._window.set_frame_px(x, y, w, h)
        frame = Frame(VSplit(*rows, gap=0.3), margin_px=_MARGIN_PX,
                      inset_px=0.5,
                      radius_px=max(0.0, self._window.corner_radius_px - 0.5))
        self._panel.set_layout(VSplit(Item(frame, weight=1)))
        self._panel.render()

    def close(self) -> None:
        """Take the window off the screen."""
        if self._window is not None:
            window, self._window, self._panel = self._window, None, None
            try:
                window.close()
            except Exception:
                logger.debug("The preview window would not close.", exc_info=True)

    def _size(self, beside) -> tuple:
        base_w, _base_h = self._backend.base_size
        return (_WIDTH_UNITS * base_w, beside[3])

    def _place(self, beside) -> tuple:
        """Right of the chooser, or left where the right side has no room;
        top-aligned with it and as tall."""
        x, y, w, h = beside
        width, height = self._size(beside)
        right = x + w + _GAP_PX
        left = x - _GAP_PX - width
        work = self._work_area(x, y)
        if work is None:
            return (right, y, width, height)
        wx, wy, ww, wh = work
        if right + width <= wx + ww:
            px = right
        elif left >= wx:
            px = left
        else:
            # No room on either side: overlap the chooser's far edge rather
            # than run off the screen.
            px = max(wx, wx + ww - width)
        return (px, max(wy, min(y, wy + wh - height)), width, height)

    def _work_area(self, x, y):
        try:
            frames = self._backend.screen_frames()
        except Exception:
            return None
        for _full, work in frames or ():
            wx, wy, ww, wh = work
            if wx <= x < wx + ww and wy <= y < wy + wh:
                return work
        return frames[0][1] if frames else None
