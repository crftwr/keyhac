"""Sources as values, and one window over several of them (discussion #112).

The hotkey is the scarce resource: an action class per kind of row means a key
per kind of row. These cover the shape that lets several kinds share one key -
and the routing that has to come with it, since Enter then means whatever the
chosen row's source says it means.
"""

import pytest

from tests.test_chooser import keyhac_engine, ui_backend  # noqa: F401

from keyhac.core.candidate import Candidate
from keyhac.core.source import CallableSource, CandidateSource, as_source


class _Fruit(CandidateSource):
    name = "Fruit"

    def __init__(self):
        self.chosen = []

    def candidates(self):
        return [Candidate(display="apple"), Candidate(display="apricot")]

    def on_chosen(self, candidate, modifier_flags):
        self.chosen.append((candidate.display, modifier_flags))


class _Tool(CandidateSource):
    name = "Tool"

    def __init__(self):
        self.chosen = []

    def candidates(self):
        return [Candidate(display="hammer")]

    def on_chosen(self, candidate, modifier_flags):
        self.chosen.append(candidate.display)


class TestSource:

    def test_choosing_routes_to_the_source(self):
        source = _Fruit()
        source.choose(source.candidates()[0], 4)
        assert source.chosen == [("apple", 4)]

    def test_a_candidate_can_override_the_source(self):
        """The unified window needs this: rows from several sources sit in
        one list and Enter has to mean whatever *that* row means."""
        source = _Fruit()
        seen = []
        source.choose(Candidate(display="odd", action=seen.append), 1)
        assert seen == [1] and source.chosen == []

    def test_a_bare_callable_is_a_source(self):
        seen = []
        source = CallableSource(lambda: [Candidate(display="x")], "Things",
                                on_chosen=lambda c, m: seen.append(c.display))
        assert [c.display for c in source.candidates()] == ["x"]
        source.choose(source.candidates()[0], 0)
        assert seen == ["x"]

    def test_a_callable_may_yield_the_old_tuples(self):
        source = CallableSource(lambda: [("*", "alpha", 1)])
        candidate = source.candidates()[0]
        assert (candidate.icon, candidate.display) == ("*", "alpha")
        assert candidate.payload == ("*", "alpha", 1)

    def test_as_source_passes_a_source_through(self):
        source = _Fruit()
        assert as_source(source) is source
        assert isinstance(as_source(lambda: []), CandidateSource)


class TestShowCandidates:

    @pytest.fixture
    def ui(self, ui_backend):
        return ui_backend

    def _open(self, sources, **kwargs):
        from keyhac.actions import ChooserAction, ShowCandidates
        action = ShowCandidates(sources, **kwargs)
        action()
        assert ChooserAction._open is not None
        return action, ChooserAction._open[1]

    def test_one_window_over_several_sources(self, ui):
        fruit, tool = _Fruit(), _Tool()
        _action, chooser = self._open([fruit, tool])
        assert [c.display for c in chooser._filtered] == [
            "apple", "apricot", "hammer"]

    def test_enter_routes_to_the_row_s_own_source(self, ui):
        fruit, tool = _Fruit(), _Tool()
        _action, chooser = self._open([fruit, tool])
        chooser._finish(chooser._filtered[2], 0)      # the Tool row
        assert tool.chosen == ["hammer"] and fruit.chosen == []

    def test_each_row_is_badged_with_its_source(self, ui):
        _action, chooser = self._open([_Fruit(), _Tool()])
        assert chooser._rows() == [("apple", "Fruit"), ("apricot", "Fruit"),
                                   ("hammer", "Tool")]

    def test_the_badge_is_drawn(self, ui):
        _action, chooser = self._open([_Fruit(), _Tool()])
        rows = ["".join(r) for r in chooser.window.snapshot()]
        assert any("apple" in r and "Fruit" in r for r in rows), rows

    def test_a_single_source_is_not_badged(self, ui):
        """With one source every row would carry the same word."""
        _action, chooser = self._open(_Fruit())
        assert chooser._badge_of is None

    def test_filtering_spans_the_sources(self, ui):
        _action, chooser = self._open([_Fruit(), _Tool()])
        chooser._on_filter_change("a")
        assert [c.display for c in chooser._filtered] == [
            "apple", "apricot", "hammer"]     # "hammer" has an 'a' too
        chooser._on_filter_change("ap")
        assert [c.display for c in chooser._filtered] == ["apple", "apricot"]

    def test_a_bare_callable_needs_no_source_class(self, ui):
        seen = []
        _action, chooser = self._open(
            lambda: [Candidate(display="branch-1")],
            on_chosen=lambda c, m: seen.append(c.display))
        chooser._finish(chooser._filtered[0], 0)
        assert seen == ["branch-1"]


class TestClipboardPresets:
    """The shipped actions are presets over the same sources - the proof the
    shape fits code that already existed."""

    def test_history_preset_wraps_the_source(self, ui_backend):
        from keyhac.actions import ShowClipboardHistory
        from keyhac.core.sources import ClipboardHistorySource
        sources = ShowClipboardHistory().sources()
        assert len(sources) == 1
        assert isinstance(sources[0], ClipboardHistorySource)

    def test_snippets_preset_keeps_its_argument(self, ui_backend):
        from keyhac.actions import ShowClipboardSnippets
        action = ShowClipboardSnippets([("📧", "me@example.com")])
        assert [c.display for c in action.sources()[0].candidates()] == [
            "me@example.com"]

    def test_a_snippet_callable_is_invoked_on_choosing(self, ui_backend,
                                                       monkeypatch):
        from keyhac.core import sources as src
        pasted = []
        monkeypatch.setattr(src._PastingSource, "paste",
                            lambda self, text, mod: pasted.append(text))
        source = src.SnippetsSource([("🕒", "Date", lambda: "2026-08-26")])
        source.choose(source.candidates()[0], 0)
        assert pasted == ["2026-08-26"]

    def test_a_snippet_callable_returning_none_pastes_nothing(
            self, ui_backend, monkeypatch):
        from keyhac.core import sources as src
        pasted = []
        monkeypatch.setattr(src._PastingSource, "paste",
                            lambda self, text, mod: pasted.append(text))
        source = src.SnippetsSource([("🕒", "Nothing", lambda: None)])
        source.choose(source.candidates()[0], 0)
        assert pasted == []


class TestScopes:
    """One key, several scopes, Tab between them - and the query survives the
    move, which is the whole reason the switch is a key and not a typed
    prefix."""

    def _open(self, scopes):
        from keyhac.actions import ChooserAction, ShowCandidates
        action = ShowCandidates(scopes)
        action()
        assert ChooserAction._open is not None
        return action, ChooserAction._open[1]

    def _scopes(self):
        from keyhac.core.source import Scope
        self.fruit, self.tool = _Fruit(), _Tool()
        return [Scope("All", [self.fruit, self.tool]),
                Scope("Fruit only", [self.fruit]),
                Scope("Tools only", [self.tool])]

    def _tab(self, chooser, shift=False):
        from puikit.event import Event, EventType
        chooser._on_event(Event(type=EventType.KEY, key="tab",
                                modifiers=frozenset({"shift"} if shift else ())))

    def test_it_opens_on_the_first_scope(self, ui_backend):
        _action, chooser = self._open(self._scopes())
        assert chooser._scope == 0
        assert [c.display for c in chooser._filtered] == [
            "apple", "apricot", "hammer"]

    def test_tab_moves_along_the_cycle(self, ui_backend):
        _action, chooser = self._open(self._scopes())
        self._tab(chooser)
        assert [c.display for c in chooser._filtered] == ["apple", "apricot"]
        self._tab(chooser)
        assert [c.display for c in chooser._filtered] == ["hammer"]

    def test_it_wraps_around(self, ui_backend):
        _action, chooser = self._open(self._scopes())
        for _ in range(3):
            self._tab(chooser)
        assert chooser._scope == 0

    def test_shift_tab_goes_back(self, ui_backend):
        _action, chooser = self._open(self._scopes())
        self._tab(chooser, shift=True)
        assert chooser._scope == 2
        assert [c.display for c in chooser._filtered] == ["hammer"]

    def test_the_query_survives_the_move(self, ui_backend):
        """Look for the same thing somewhere else without retyping it - the
        one thing a typed prefix cannot do without editing what is already
        there. Typed for real: setting the filter directly would leave the
        field empty and the assertion would pass on nothing."""
        from puikit.event import Event, EventType
        _action, chooser = self._open(self._scopes())
        chooser._on_event(Event(type=EventType.KEY, key="a", char="a"))
        assert chooser._edit.text == "a"
        assert [c.display for c in chooser._filtered] == [
            "apple", "apricot", "hammer"]
        self._tab(chooser)
        assert chooser._edit.text == "a"
        assert [c.display for c in chooser._filtered] == ["apple", "apricot"]

    def test_the_current_scope_is_named_on_screen(self, ui_backend):
        _action, chooser = self._open(self._scopes())
        assert chooser._scope_label.text() == "‹ All ›"
        self._tab(chooser)
        assert chooser._scope_label.text() == "‹ Fruit only ›"
        rows = ["".join(r) for r in chooser.window.snapshot()]
        assert any("Fruit only" in r for r in rows), rows

    def test_switching_re_proposes_nothing(self, ui_backend):
        from puikit.event import Event, EventType
        _action, chooser = self._open(self._scopes())
        chooser._on_event(Event(type=EventType.KEY, key="down"))
        assert chooser.in_list
        self._tab(chooser)
        assert not chooser.in_list, "the rows are different ones now"

    def test_choosing_still_routes_to_the_right_source(self, ui_backend):
        _action, chooser = self._open(self._scopes())
        self._tab(chooser)
        self._tab(chooser)                     # Tools only
        chooser._finish(chooser._filtered[0], 0)
        assert self.tool.chosen == ["hammer"] and self.fruit.chosen == []

    def test_reopening_starts_at_the_first_scope_again(self, ui_backend):
        action, chooser = self._open(self._scopes())
        self._tab(chooser)
        assert chooser._scope == 1
        action()                               # toggles closed
        action()                               # and open again
        from keyhac.actions import ChooserAction
        assert ChooserAction._open[1]._scope == 0

    def test_one_scope_shows_no_switcher(self, ui_backend):
        _action, chooser = self._open([_Fruit(), _Tool()])
        assert chooser._scopes == []
        assert chooser._scope_label.text() == ""

    def test_the_arrows_are_clickable(self, ui_backend):
        """The pointer is sometimes already in hand. A click reaches the
        popup (overlay_input="mouse" on macOS, WS_EX_NOACTIVATE on Windows)
        without the application underneath losing anything."""
        from puikit.event import Event, EventType
        _action, chooser = self._open(self._scopes())
        switcher = chooser._scope_label
        chooser.panel.render()                 # the widget learns its width
        width = switcher._width
        assert width > 0

        switcher.handle_event(Event(type=EventType.MOUSE_CLICK,
                                    x=width - 1, y=0, button="left"))
        assert chooser._scope == 1, "the right arrow moves forward"

        switcher.handle_event(Event(type=EventType.MOUSE_CLICK,
                                    x=0, y=0, button="left"))
        assert chooser._scope == 0, "the left arrow moves back"

    def test_clicking_the_switcher_keeps_the_query(self, ui_backend):
        from puikit.event import Event, EventType
        _action, chooser = self._open(self._scopes())
        chooser._on_event(Event(type=EventType.KEY, key="a", char="a"))
        chooser.panel.render()
        chooser._scope_label.handle_event(
            Event(type=EventType.MOUSE_CLICK,
                  x=chooser._scope_label._width - 1, y=0, button="left"))
        assert chooser._edit.text == "a"
        assert [c.display for c in chooser._filtered] == ["apple", "apricot"]

    def test_the_switcher_does_not_take_the_focus(self, ui_backend):
        """Clicking it must not pull the focus out of the filter field."""
        assert not getattr(
            __import__("keyhac.ui.scope_switcher", fromlist=["ScopeSwitcher"])
            .ScopeSwitcher, "focusable", False)

    def test_a_click_routes_through_the_window(self, ui_backend):
        """Not just the widget in isolation: the search row has to place it
        where a click can find it."""
        from puikit.event import Event, EventType
        _action, chooser = self._open(self._scopes())
        chooser.panel.render()
        rows = ["".join(r) for r in chooser.window.snapshot()]
        line = next(i for i, r in enumerate(rows) if "All" in r)
        column = rows[line].index("All")
        chooser._on_event(Event(type=EventType.MOUSE_DOWN, x=column, y=line,
                                button="left"))
        chooser._on_event(Event(type=EventType.MOUSE_UP, x=column, y=line,
                                button="left"))
        assert chooser._scope != 0, "the click never reached the switcher"
