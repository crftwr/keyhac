"""ClipboardHistory with a fake provider."""

from keyhac.core.clipboard_history import ClipboardHistory
from keyhac.platform.base import ClipboardProvider


class FakeProvider(ClipboardProvider):
    def __init__(self):
        self.text = None
        self.changed = False

    def get_text(self):
        return self.text

    def set_text(self, s):
        self.text = s

    def poll(self):
        changed, self.changed = self.changed, False
        return changed


def make(tmp_path):
    return FakeProvider(), str(tmp_path / "clipboard.json")


class TestClipboardHistory:

    def test_capture_and_order(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        for text in ("one", "two", "three"):
            provider.text = text
            history.on_clipboard_changed()
        assert [s for s, _ in history.items()] == ["three", "two", "one"]
        assert history.get_current() == "three"

    def test_duplicates_move_to_front(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        for text in ("a", "b", "a"):
            history.add_item(text)
        assert [s for s, _ in history.items()] == ["a", "b"]

    def test_set_current_updates_os_clipboard(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        history.set_current("hello")
        assert provider.text == "hello"
        assert history.get_current() == "hello"

    def test_persistence_round_trip(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        history.add_item("first")
        history.add_item("second")
        assert history.dirty
        history.flush()
        assert not history.dirty

        reloaded = ClipboardHistory(FakeProvider(), path)
        assert [s for s, _ in reloaded.items()] == ["second", "first"]

    def test_max_items_cap(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        old_cap = ClipboardHistory.max_items
        ClipboardHistory.max_items = 3
        try:
            for i in range(5):
                history.add_item(f"item{i}")
            assert len(list(history.items())) == 3
            assert history.get_current() == "item4"
        finally:
            ClipboardHistory.max_items = old_cap

    def test_labels_are_flattened(self, tmp_path):
        provider, path = make(tmp_path)
        history = ClipboardHistory(provider, path)
        history.add_item("line one\n  line two\t\tend")
        _s, label = next(history.items())
        assert label == "line one line two end"
