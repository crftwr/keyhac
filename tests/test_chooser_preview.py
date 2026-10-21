"""The chooser's preview window (issue #262), against PuiKit's MemoryBackend.

The memory backend cannot hand work back to a main thread, so the preview is
made inline once the rest delay fires - the rest of the shape (which row, when,
and what closes it) is the same as on the real backends."""

import io

import pytest

from tests.test_chooser import keyhac_engine, ui_backend  # noqa: F401

from keyhac.core.candidate import Candidate, CandidatePreview
from keyhac.core.source import CandidateSource


class _Notes(CandidateSource):
    name = "Notes"

    def __init__(self, image=None):
        self.asked = []
        self.image = image

    def candidates(self):
        return [Candidate(display="short", payload=None),
                Candidate(display="long", payload="line one\nline two")]

    def preview(self, candidate):
        self.asked.append(candidate.display)
        if candidate.payload is None:
            return None
        return CandidatePreview(title="Note", text=candidate.payload,
                                image=self.image, fields=(("Lines", "2"),))


def _text(window) -> str:
    return "\n".join("".join(row) for row in window.grid)


def _open(source):
    from keyhac.actions import ChooserAction, ShowCandidates
    action = ShowCandidates([source])
    action()
    return ChooserAction._open[1]


def _key(chooser, key, char=None):
    from puikit.event import Event, EventType
    chooser._on_event(Event(type=EventType.KEY, key=key, char=char))


def _preview_windows(backend):
    return [w for w in backend.windows
            if w.title == "Keyhac Preview" and not w._closed]


class TestPreviewWindow:

    def test_nothing_is_asked_while_the_field_has_the_focus(self, ui_backend):
        source = _Notes()
        _open(source)
        ui_backend.fire_timers()
        assert source.asked == []
        assert _preview_windows(ui_backend) == []

    def test_the_selected_row_is_previewed_once_it_rests(self, ui_backend):
        source = _Notes()
        chooser = _open(source)
        _key(chooser, "down")
        _key(chooser, "down")
        # Not yet: the selection has not rested.
        assert source.asked == []
        ui_backend.fire_timers()
        # Only the row it rested on, not the one passed on the way.
        assert source.asked == ["long"]
        [window] = _preview_windows(ui_backend)
        shown = _text(window)
        assert "Note" in shown and "line two" in shown and "Lines" in shown

    def test_a_row_with_no_preview_shows_no_window(self, ui_backend):
        source = _Notes()
        chooser = _open(source)
        _key(chooser, "down")
        ui_backend.fire_timers()
        assert source.asked == ["short"]
        assert _preview_windows(ui_backend) == []

    def test_moving_on_closes_it(self, ui_backend):
        chooser = _open(_Notes())
        _key(chooser, "down")
        _key(chooser, "down")
        ui_backend.fire_timers()
        assert _preview_windows(ui_backend)
        _key(chooser, "up")
        assert _preview_windows(ui_backend) == []

    def test_closing_the_chooser_closes_it(self, ui_backend):
        chooser = _open(_Notes())
        _key(chooser, "down")
        _key(chooser, "down")
        ui_backend.fire_timers()
        _key(chooser, "escape")
        assert _preview_windows(ui_backend) == []

    def test_a_late_preview_for_a_closed_chooser_is_dropped(self, ui_backend):
        source = _Notes()
        chooser = _open(source)
        _key(chooser, "down")
        _key(chooser, "down")
        _key(chooser, "escape")
        ui_backend.fire_timers()
        assert source.asked == []
        assert _preview_windows(ui_backend) == []

    def test_space_in_the_list_toggles_previews(self, ui_backend):
        source = _Notes()
        chooser = _open(source)
        _key(chooser, "down")
        _key(chooser, "down")
        ui_backend.fire_timers()
        _key(chooser, "space", " ")
        assert _preview_windows(ui_backend) == []
        assert chooser._edit.text == ""      # not typed into the query
        assert chooser.previews_on is False
        _key(chooser, "space", " ")
        ui_backend.fire_timers()
        assert _preview_windows(ui_backend)

    def test_space_in_the_field_is_still_a_space(self, ui_backend):
        chooser = _open(_Notes())
        _key(chooser, "space", " ")
        assert chooser.previews_on is True

    def test_an_image_is_decoded(self, ui_backend):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (40, 20), (255, 0, 0)).save(buffer, "PNG")
        chooser = _open(_Notes(image=buffer.getvalue()))
        _key(chooser, "down")
        _key(chooser, "down")
        ui_backend.fire_timers()
        assert _preview_windows(ui_backend)

    def test_a_source_without_previews_is_never_asked(self, ui_backend):
        from tests.test_sources import _Fruit
        chooser = _open(_Fruit())
        _key(chooser, "down")
        ui_backend.fire_timers()
        assert _preview_windows(ui_backend) == []


class TestDecode:

    def test_scaled_to_fit_and_rgba(self):
        from PIL import Image
        from keyhac.ui.preview import decode_image
        buffer = io.BytesIO()
        Image.new("RGB", (400, 100)).save(buffer, "PNG")
        raster = decode_image(buffer.getvalue(), (200, 200))
        assert raster.size == (200, 50)
        assert raster.nbytes == 200 * 50 * 4

    def test_a_windows_dib_decodes_once_wrapped(self):
        import struct
        from keyhac.platform.win.clipboard import dib_to_bmp
        from keyhac.ui.preview import decode_image
        header = struct.pack("<IiiHHIIiiII", 40, 3, 2, 1, 24, 0, 0, 0, 0, 0, 0)
        rows = (b"\x00\x00\xff" * 3 + b"\x00\x00\x00") * 2   # padded to 4
        raster = decode_image(dib_to_bmp(header + rows), (100, 100))
        assert raster.size == (3, 2)

    def test_garbage_is_none(self):
        from keyhac.ui.preview import decode_image
        assert decode_image(b"not an image", (10, 10)) is None


class TestSourcePreviews:

    def test_clipboard_text_previews_only_what_the_row_cannot_show(
            self, keyhac_engine, tmp_path):
        from keyhac.core.clipboard_history import ClipboardHistory
        from keyhac.core.sources import ClipboardHistorySource
        from tests.test_clipboard_snapshots import SnapshotProvider
        keymap = keyhac_engine.keymap
        keymap._clipboard_history = ClipboardHistory(
            SnapshotProvider(), str(tmp_path / "c.json"))
        keymap.clipboard_history.add_item("short")
        keymap.clipboard_history.add_item("one\ntwo\nthree")
        source = ClipboardHistorySource()
        multi, short = source.candidates()
        assert source.preview(short) is None
        preview = source.preview(multi)
        assert preview.text == "one\ntwo\nthree"
        assert ("Lines", "3") in preview.fields

    def test_a_tool_previews_its_result(self, keyhac_engine, tmp_path):
        from keyhac.actions import ShowClipboardTools
        from keyhac.core.clipboard_history import ClipboardHistory
        from tests.test_clipboard_snapshots import SnapshotProvider
        keymap = keyhac_engine.keymap
        keymap._clipboard_history = ClipboardHistory(
            SnapshotProvider(), str(tmp_path / "c.json"))
        keymap.clipboard_history.set_current("a\nb")
        source = ShowClipboardTools(
            [("🔄", "Quote", ShowClipboardTools.quote)]).sources()[0]
        preview = source.preview(source.candidates()[0])
        assert preview.title == "Result" and preview.text == "> a\n> b"

    def test_an_action_previews_its_docstring(self, tmp_path):
        from keyhac.core.sources import ActionsSource
        from keyhac.mcp.extensions import ActionClass
        path = tmp_path / "tool.py"
        path.write_text(
            "class Translate(ThreadedAction):\n"
            "    '''Translate the clipboard.\n\n    Uses the configured engine.\n    '''\n")
        action = ActionClass("tool", "Translate", str(path), [], "Translate the clipboard.")
        preview = ActionsSource().preview(Candidate(display="x", payload=action))
        assert "Uses the configured engine." in preview.text
        assert ("Address", "tool.Translate") in preview.fields
