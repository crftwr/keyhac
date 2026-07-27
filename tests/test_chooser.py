"""Chooser single-instance behavior (issue #3: a second invocation must not
stack another chooser window). UI is tested against puikit's MemoryBackend."""

import pytest

from keyhac.actions import ChooserAction
from keyhac.ui import runtime


class _Items(ChooserAction):

    def __init__(self):
        self.chosen = []

    def list_items(self):
        return [("*", "alpha", "a"), ("*", "beta", "b")]

    def on_chosen(self, item, modifier_flags):
        self.chosen.append(item)


@pytest.fixture
def ui_backend(engine):
    def configure(keymap):
        keymap.define_keytable(focus_path_pattern="*")

    engine(configure)  # registers the Keymap instance the actions look up
    from puikit.backends.memory_backend import MemoryBackend
    backend = MemoryBackend(width=100, height=30)
    backend.open()
    runtime.backend = backend
    yield backend
    runtime.backend = None
    ChooserAction._open = None
    backend.close()


class TestChooserSingleInstance:

    def test_same_action_toggles(self, ui_backend):
        action = _Items()
        action()
        assert ChooserAction._open is not None
        first = ChooserAction._open[1]
        assert not first._done

        action()  # same action again: close, do not reopen
        assert ChooserAction._open is None
        assert first._done

    def test_different_action_replaces(self, ui_backend):
        first_action, second_action = _Items(), _Items()
        first_action()
        first = ChooserAction._open[1]

        second_action()
        assert ChooserAction._open is not None
        assert ChooserAction._open[0] is second_action
        assert ChooserAction._open[1] is not first
        assert first._done

    def test_selection_clears_registry(self, ui_backend):
        action = _Items()
        action()
        chooser = ChooserAction._open[1]
        chooser._finish(("*", "alpha", "a"), 0)
        assert ChooserAction._open is None
        assert action.chosen == [("*", "alpha", "a")]

    def test_cancel_clears_registry(self, ui_backend):
        action = _Items()
        action()
        chooser = ChooserAction._open[1]
        chooser._finish(None, 0)
        assert ChooserAction._open is None
        assert action.chosen == []
