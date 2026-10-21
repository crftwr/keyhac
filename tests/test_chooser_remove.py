"""Shift-Delete removes the selected row, when its source can (issue #266)."""

import json

from tests.test_chooser import keyhac_engine, ui_backend  # noqa: F401
from tests.test_chooser_preview import _key, _open
from tests.test_clipboard_snapshots import SnapshotProvider

from keyhac.core.clipboard_history import ClipboardHistory


def _history(keyhac_engine, tmp_path, *texts):
    keymap = keyhac_engine.keymap
    keymap._clipboard_history = ClipboardHistory(
        SnapshotProvider(), str(tmp_path / "clipboard.json"))
    for text in texts:
        keymap.clipboard_history.add_item(text)
    return keymap.clipboard_history


def _shift_delete(chooser):
    from puikit.event import Event, EventType
    chooser._on_event(Event(type=EventType.KEY, key="delete",
                            modifiers=frozenset({"shift"})))


def _labels(chooser):
    return [c.display for c in chooser._filtered]


class TestRemove:

    def test_shift_delete_removes_the_entry_for_good(self, keyhac_engine,
                                                      ui_backend, tmp_path):
        from keyhac.core.sources import ClipboardHistorySource
        history = _history(keyhac_engine, tmp_path, "one", "two", "three")
        chooser = _open(ClipboardHistorySource())
        _key(chooser, "down")
        _key(chooser, "down")                 # "two"
        _shift_delete(chooser)
        assert _labels(chooser) == ["three", "one"]
        # The row below took its place, so the key goes on down the list.
        assert chooser._selected().display == "one"
        assert [s for s, _ in history.items()] == ["three", "one"]
        history.flush()
        saved = json.load(open(tmp_path / "clipboard.json"))["clipboard_history"]
        assert [item["data"] for item in saved] == ["three", "one"]

    def test_removing_the_last_row_returns_to_the_field(self, keyhac_engine,
                                                        ui_backend, tmp_path):
        from keyhac.core.sources import ClipboardHistorySource
        _history(keyhac_engine, tmp_path, "only")
        chooser = _open(ClipboardHistorySource())
        _key(chooser, "down")
        _shift_delete(chooser)
        assert _labels(chooser) == []
        assert not chooser.in_list

    def test_a_switched_page_does_not_bring_it_back(self, keyhac_engine,
                                                    ui_backend, tmp_path):
        from keyhac.actions import ChooserAction, ShowCandidates
        from keyhac.core.source import ChooserPage
        from keyhac.core.sources import ClipboardHistorySource
        _history(keyhac_engine, tmp_path, "keep", "drop")
        clipboard = ClipboardHistorySource()
        ShowCandidates([ChooserPage("A", [clipboard]),
                        ChooserPage("B", [clipboard])])()
        chooser = ChooserAction._open[1]
        _key(chooser, "down")                 # "drop"
        _shift_delete(chooser)
        chooser.switch_page(1)
        assert _labels(chooser) == ["keep"]

    def test_a_source_that_cannot_remove_keeps_the_row(self, ui_backend):
        from tests.test_sources import _Fruit
        chooser = _open(_Fruit())
        before = _labels(chooser)
        _key(chooser, "down")
        _shift_delete(chooser)
        assert _labels(chooser) == before

    def test_bare_delete_is_still_the_querys(self, keyhac_engine, ui_backend,
                                             tmp_path):
        from keyhac.core.sources import ClipboardHistorySource
        _history(keyhac_engine, tmp_path, "one", "two")
        chooser = _open(ClipboardHistorySource())
        _key(chooser, "down")
        _key(chooser, "delete")
        assert _labels(chooser) == ["two", "one"]
        assert not chooser.in_list

    def test_the_current_clipboard_row_cannot_be_removed(self, keyhac_engine,
                                                         ui_backend, tmp_path):
        from keyhac.core.sources import ClipboardHistorySource
        from tests.test_clipboard_snapshots import copy
        from keyhac.platform.base import ClipboardSnapshot
        history = _history(keyhac_engine, tmp_path, "older")
        copy((history._provider, history), ClipboardSnapshot(text="pw"),
             concealed=True)
        chooser = _open(ClipboardHistorySource())
        _key(chooser, "down")
        _shift_delete(chooser)
        assert _labels(chooser)[0] == "Current clipboard (hidden)"


class TestHistoryRemove:

    def test_remove_item_by_text(self, tmp_path):
        history = ClipboardHistory(SnapshotProvider(), str(tmp_path / "c.json"))
        history.add_item("a")
        history.add_item("b")
        history.remove_item("a")
        history.remove_item("not there")
        assert [s for s, _ in history.items()] == ["b"]

    def test_the_current_entry_is_not_re_recorded_until_a_change(self, tmp_path):
        from tests.test_clipboard_snapshots import copy
        from keyhac.platform.base import ClipboardSnapshot
        provider = SnapshotProvider()
        history = ClipboardHistory(provider, str(tmp_path / "c.json"))
        copy((provider, history), ClipboardSnapshot(text="oops"))
        history.remove(next(history.entries()))
        assert list(history.entries()) == []
        assert provider.snapshot.text == "oops"      # the clipboard keeps it
