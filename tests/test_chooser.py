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


class TestChooserScrolling:
    """Issue #27: the chooser routes keys itself and assigns the list's
    `selected` directly, so the scroll has to come with the assignment -
    puikit >= 1.0.12 scrolls the selection into view on assignment."""

    def test_selection_moved_past_the_viewport_stays_visible(self, ui_backend):
        from puikit.event import Event, EventType
        from keyhac.ui.chooser import ChooserWindow

        items = [("*", f"entry {i:02}", i) for i in range(40)]
        chooser = ChooserWindow(ui_backend, items)
        for _ in range(30):
            chooser._on_event(Event(type=EventType.KEY, key="down"))
        assert chooser._list.selected == 30
        rows = ["".join(row) for row in chooser.window.snapshot()]
        assert any("entry 30" in row for row in rows), \
            "the selected row scrolled out of view (issue #27)"


class TestChooserCentering:
    """Issue #4: the chooser centers on the focused window's frame.

    The memory backend creates 72x20 windows at (160, 160) with 1 px per
    cell, so the geometry below is exact."""

    def _window(self, ui_backend, **kwargs):
        from keyhac.ui.chooser import ChooserWindow
        return ChooserWindow(ui_backend, [("*", "a", 1)], **kwargs)

    def test_default_position_untouched(self, ui_backend):
        chooser = self._window(ui_backend)
        assert chooser.window.frame_px() == (160.0, 160.0, 72.0, 20.0)

    def test_centers_on_rect(self, ui_backend):
        chooser = self._window(ui_backend, center_on=(100, 100, 400, 300))
        # center (300, 250) minus half of 72x20
        assert chooser.window.frame_px() == (264.0, 240.0, 72.0, 20.0)

    def test_clamped_to_screen(self, ui_backend):
        chooser = self._window(ui_backend, center_on=(100, 100, 400, 300),
                               clamp_to=(0, 0, 300, 250))
        assert chooser.window.frame_px() == (228.0, 230.0, 72.0, 20.0)

    def test_clamped_at_origin(self, ui_backend):
        chooser = self._window(ui_backend, center_on=(-500, -500, 100, 100),
                               clamp_to=(0, 0, 800, 600))
        assert chooser.window.frame_px() == (0.0, 0.0, 72.0, 20.0)
