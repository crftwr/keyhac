"""Clipboard history beyond text: snapshots, the current-clipboard row, and
plain-text paste (issue #19).  Hermetic: a fake provider holds snapshots."""

import json
import struct

import pytest

from keyhac.core.clipboard_history import (
    CONCEALED, UNSUPPORTED, ClipboardHistory,
)
from keyhac.core.const import MODKEY_ALT, MODKEY_SHIFT
from keyhac.platform.base import ClipboardProvider, ClipboardSnapshot
from keyhac.platform.win import clipboard as winclip
from tests.test_chooser import keyhac_engine, ui_backend  # noqa: F401


class SnapshotProvider(ClipboardProvider):
    """Holds one snapshot, the way the OS clipboard does."""

    def __init__(self):
        self.snapshot = None
        self.concealed = False
        self.restored = []
        self.texts = []

    def get_text(self):
        return self.snapshot.text if self.snapshot else None

    def set_text(self, s, concealed=False):
        self.texts.append(s)
        self.snapshot = ClipboardSnapshot(text=s)

    def poll(self):
        return False

    def is_concealed(self):
        return self.concealed

    def get_snapshot(self, max_format_size=None):
        return self.snapshot

    def set_snapshot(self, snapshot):
        self.restored.append(snapshot)
        self.snapshot = snapshot


def rich(text):
    return ClipboardSnapshot(kind="rich", text=text,
                             items=[{"public.rtf": b"{\\rtf1 " + text.encode() + b"}"}])


def image(seed=b"px", size=(64, 32)):
    return ClipboardSnapshot(kind="image", image_size=size,
                             items=[{"public.png": seed * 10}])


def files(*paths):
    return ClipboardSnapshot(kind="files", files=tuple(paths),
                             items=[{"public.file-url": p.encode()} for p in paths])


@pytest.fixture
def board(tmp_path):
    provider = SnapshotProvider()
    history = ClipboardHistory(provider, str(tmp_path / "clipboard.json"))
    return provider, history


def copy(board, snapshot, concealed=False):
    provider, history = board
    provider.snapshot, provider.concealed = snapshot, concealed
    history.on_clipboard_changed()


class TestEntries:

    def test_an_image_without_text_is_an_entry(self, board):
        _provider, history = board
        copy(board, image())
        entry = next(history.entries())
        assert entry.snapshot.kind == "image"
        assert entry.label == "Image 64 × 32"
        # items() is the text API, and an image has none.
        assert list(history.items()) == []

    def test_files_are_labelled_by_name_and_keyed_by_path(self, board):
        _provider, history = board
        copy(board, files("/a/one.txt", "/b/two.png"))
        copy(board, files("/a/one.txt", "/b/two.png"))
        entries = list(history.entries())
        assert len(entries) == 1
        assert entries[0].label == "one.txt, two.png"

    def test_the_same_text_rich_or_plain_is_one_entry(self, board):
        _provider, history = board
        copy(board, ClipboardSnapshot(text="hello"))
        copy(board, rich("hello"))
        entries = list(history.entries())
        assert len(entries) == 1
        assert entries[0].snapshot.kind == "rich"

    def test_text_that_lost_formats_is_marked(self, board):
        _provider, history = board
        copy(board, ClipboardSnapshot(text="cells", lossy=True))
        assert next(history.entries()).label == "cells (text only)"
        # A rich entry that dropped something extra still has its formatting.
        copy(board, ClipboardSnapshot(kind="rich", text="web", lossy=True,
                                      items=[{"public.html": b"<b>web</b>"}]))
        assert next(history.entries()).label == "web"

    def test_current_tracks_what_the_clipboard_holds(self, board):
        _provider, history = board
        copy(board, ClipboardSnapshot(text="one"))
        assert history.current().text == "one"
        copy(board, ClipboardSnapshot(text="secret"), concealed=True)
        assert history.current() == CONCEALED
        copy(board, ClipboardSnapshot(kind=None, lossy=True))
        assert history.current() == UNSUPPORTED
        copy(board, None)
        assert history.current() is None
        # Neither the hidden nor the unreadable content became an entry.
        assert [e.text for e in history.entries()] == ["one"]


class TestSessionBudget:

    def test_oldest_rich_entries_fall_back_to_text(self, board, monkeypatch):
        _provider, history = board
        monkeypatch.setattr(ClipboardHistory, "max_session_data_size", 20)
        copy(board, rich("first"))     # 13 bytes of RTF
        copy(board, rich("second"))    # 14 more: over budget
        newest, oldest = list(history.entries())
        assert newest.snapshot.items
        assert not oldest.snapshot.items
        assert oldest.text == "first"
        assert oldest.label == "first (text only)"

    def test_an_image_past_the_budget_is_forgotten(self, board, monkeypatch):
        _provider, history = board
        monkeypatch.setattr(ClipboardHistory, "max_session_data_size", 25)
        copy(board, image(b"aa"))
        copy(board, image(b"bb"))
        assert [e.snapshot.items[0]["public.png"][:2]
                for e in history.entries()] == [b"bb"]


class TestPersistence:

    def test_text_and_files_survive_a_restart_and_images_do_not(self, board,
                                                                tmp_path):
        _provider, history = board
        copy(board, rich("words"))
        copy(board, image())
        copy(board, files("/a/one.txt"))
        history.flush()
        saved = json.load(open(tmp_path / "clipboard.json"))["clipboard_history"]
        assert saved == [{"type": "files", "data": ["/a/one.txt"]},
                         {"type": "string", "data": "words"}]

        reloaded = ClipboardHistory(SnapshotProvider(),
                                    str(tmp_path / "clipboard.json"))
        kinds = [(e.snapshot.kind, e.snapshot.files, e.text)
                 for e in reloaded.entries()]
        assert kinds == [("files", ("/a/one.txt",), None),
                         ("text", (), "words")]


@pytest.fixture
def wired(keyhac_engine, ui_backend, tmp_path, monkeypatch):
    """The history on a live Keymap, with the paste keystroke captured."""
    from keyhac.core import sources as src

    provider = SnapshotProvider()
    keymap = keyhac_engine.keymap
    keymap._clipboard_history = ClipboardHistory(
        provider, str(tmp_path / "clipboard.json"))
    pasted = []
    monkeypatch.setattr(src._PastingSource, "paste_clipboard",
                        lambda self, mod: pasted.append(
                            provider.snapshot if not mod & MODKEY_SHIFT else "set-only"))
    return provider, keymap.clipboard_history, pasted


class TestHistorySource:

    def _rows(self):
        from keyhac.core.sources import ClipboardHistorySource
        source = ClipboardHistorySource()
        return source, source.candidates()

    def test_choosing_an_older_entry_restores_every_format(self, wired):
        provider_, history_, pasted = wired
        copy((provider_, history_), rich("styled"))
        copy((provider_, history_), ClipboardSnapshot(text="later"))
        source, rows = self._rows()
        source.choose(rows[1], 0)
        assert provider_.restored[-1].kind == "rich"
        assert pasted[-1].text == "styled"

    def test_choosing_the_current_entry_leaves_the_clipboard_alone(self, wired):
        provider, history, pasted = wired
        copy((provider, history), ClipboardSnapshot(text="cells", lossy=True))
        source, rows = self._rows()
        source.choose(rows[0], 0)
        # Re-setting it would drop the formats the history did not keep.
        assert provider.restored == [] and provider.texts == []
        assert len(pasted) == 1

    def test_option_enter_pastes_the_text_alone(self, wired):
        provider, history, pasted = wired
        copy((provider, history), rich("styled"))
        source, rows = self._rows()
        source.choose(rows[0], MODKEY_ALT)
        assert provider.texts == ["styled"]
        assert provider.snapshot.kind == "text"

    def test_option_enter_on_an_image_pastes_nothing(self, wired):
        provider, history, pasted = wired
        copy((provider, history), image())
        source, rows = self._rows()
        source.choose(rows[0], MODKEY_ALT)
        assert pasted == []

    @pytest.mark.parametrize("concealed, snapshot, label", [
        (True, ClipboardSnapshot(text="hunter2"), "Current clipboard (hidden)"),
        (False, ClipboardSnapshot(kind=None, lossy=True),
         "Current clipboard (cannot be shown)"),
    ])
    def test_unrecorded_content_gets_a_row_that_pastes_it_untouched(
            self, wired, concealed, snapshot, label):
        provider, history, pasted = wired
        copy((provider, history), ClipboardSnapshot(text="older"))
        copy((provider, history), snapshot, concealed=concealed)
        source, rows = self._rows()
        assert rows[0].display == label
        assert "hunter2" not in " ".join(r.match_text for r in rows)
        source.choose(rows[0], 0)
        assert provider.restored == [] and provider.texts == []
        assert pasted == [snapshot]

    def test_rows_carry_kind_icons(self, wired):
        provider, history, _pasted = wired
        copy((provider, history), files("/x/report.pdf"))
        copy((provider, history), image())
        _source, rows = self._rows()
        assert [r.icon for r in rows] == ["🖼️", "📄"]
        assert rows[1].match_text == "/x/report.pdf"


class TestToolsOnNonText:

    def test_a_tool_on_an_image_clipboard_converts_nothing(self, wired,
                                                           monkeypatch):
        from keyhac.actions import ShowClipboardTools
        from keyhac.core import sources as src
        provider, history, _pasted = wired
        copy((provider, history), ClipboardSnapshot(text="old text"))
        copy((provider, history), image())
        converted = []
        monkeypatch.setattr(src._PastingSource, "paste",
                            lambda self, text, mod: converted.append(text))
        source = ShowClipboardTools(
            [("🔄", "Quote", ShowClipboardTools.quote)]).sources()[0]
        source.choose(source.candidates()[0], 0)
        # Not the older text: that is not what the clipboard holds.
        assert converted == []


class TestWindowsFormats:
    """The byte-level helpers of the Windows provider, which run anywhere."""

    def test_hdrop_round_trip(self):
        paths = ("C:\\a.txt", "D:\\日本語\\b.png")
        assert winclip.parse_hdrop(winclip.build_hdrop(paths)) == paths

    def test_a_cut_comes_back_as_a_copy(self):
        move = struct.pack("<I", winclip.DROPEFFECT_MOVE)
        assert winclip.as_copy_effect(move) == struct.pack("<I", winclip.DROPEFFECT_COPY)
        copy_ = struct.pack("<I", winclip.DROPEFFECT_COPY | 4)
        assert winclip.as_copy_effect(copy_) == copy_

    def test_image_sizes_from_headers(self):
        dib = struct.pack("<Iii", 40, 1280, -720)     # top-down DIB
        png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 300, 200)
        assert winclip.image_size("CF_DIB", dib) == (1280, 720)
        assert winclip.image_size("PNG", png) == (300, 200)

    def test_describe_picks_the_kind(self):
        assert winclip.describe({"CF_HDROP": winclip.build_hdrop(["C:\\x"])},
                                None, False).kind == "files"
        assert winclip.describe({"PNG": b""}, None, False).kind == "image"
        assert winclip.describe({"HTML Format": b"x", "CF_UNICODETEXT": b""},
                                "t", False).kind == "rich"
        assert winclip.describe({}, None, True).kind is None

    def test_unicode_text_stops_at_its_terminator(self):
        data = "日本\x00junk".encode("utf-16-le")
        assert winclip.decode_unicode_text(data) == "日本"
