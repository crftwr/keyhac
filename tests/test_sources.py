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


class _FakeMenuElement:
    """A menu tree in the shape both platforms expose: a bar of items, each
    opening a menu of items, some of which open another."""

    def __init__(self, role, name="", kids=(), enabled=True, shortcut=None):
        self._role = role
        self._name = name
        self._kids = list(kids)
        self._enabled = enabled
        self._shortcut = shortcut or {}
        self.pressed = []

    def describe(self):
        return {"role": self._role, "name": self._name}

    def children(self):
        return self._kids

    def get_attribute_value(self, name):
        if name in ("AXEnabled", "IsEnabled"):
            return self._enabled
        return self._shortcut.get(name)

    def perform_action(self, name):
        self.pressed.append(name)
        return name == "AXPress"


def _menu(*kids):
    return _FakeMenuElement("AXMenu", kids=kids)


def _item(name, kids=(), **kw):
    return _FakeMenuElement("AXMenuItem", name, kids=kids, **kw)


class TestMenuItemsSource:
    """Every command in the front application's menus, flattened."""

    def _bar(self):
        self.save_as = _item("Save As…", shortcut={
            "AXMenuItemCmdChar": "S", "AXMenuItemCmdModifiers": 1})
        return _FakeMenuElement("AXMenuBar", kids=[
            _FakeMenuElement("AXMenuBarItem", "File", kids=[_menu(
                _item("New", shortcut={"AXMenuItemCmdChar": "N",
                                       "AXMenuItemCmdModifiers": 0}),
                _item("Export", kids=[_menu(self.save_as,
                                            _item("As PDF…"))]),
                _item("Print", enabled=False),
            )]),
            _FakeMenuElement("AXMenuBarItem", "Edit", kids=[_menu(
                _item("Undo"),
            )]),
        ])

    def _rows(self):
        from keyhac.core.sources import _walk_menu
        rows = []
        _walk_menu(self._bar(), (), rows, 0)
        return rows

    def test_only_leaves_are_offered(self):
        """A row that merely opens another menu is not a command, and a list
        of them would be a worse menu bar rather than a better one."""
        displays = [c.display for c in self._rows()]
        assert "File › Export" not in displays
        assert "File › Export › As PDF…" in displays

    def test_a_row_reads_as_the_path_to_it(self):
        assert "File › Export › Save As…" in [c.display for c in self._rows()]

    def test_a_disabled_item_is_skipped(self):
        assert "File › Print" not in [c.display for c in self._rows()]

    def test_every_menu_is_walked(self):
        assert "Edit › Undo" in [c.display for c in self._rows()]

    def test_the_shortcut_travels_with_the_row(self):
        by_name = {c.display: c for c in self._rows()}
        assert by_name["File › New"].extras["shortcut"] == "Cmd-N"
        assert by_name["File › Export › Save As…"].extras["shortcut"] == \
            "Cmd-Shift-S"

    def test_an_item_without_a_shortcut_says_so(self):
        by_name = {c.display: c for c in self._rows()}
        assert by_name["File › Export › As PDF…"].extras["shortcut"] == ""

    def test_the_modifier_mask_clears_command_rather_than_setting_it(self):
        """0x08 is *not Command* - read off real menus, because a plain Cmd-D
        reports 0 and Ctrl-Tab reports 0x08 | 0x04."""
        from keyhac.core.sources import _menu_shortcut
        item = _FakeMenuElement("AXMenuItem", "x", shortcut={
            "AXMenuItemCmdChar": "T", "AXMenuItemCmdModifiers": 0x08 | 0x04})
        assert _menu_shortcut(item) == "Ctrl-T"

    def test_fn_is_the_high_bit(self):
        from keyhac.core.sources import _menu_shortcut
        item = _FakeMenuElement("AXMenuItem", "x", shortcut={
            "AXMenuItemCmdChar": "F", "AXMenuItemCmdModifiers": 0x18})
        assert _menu_shortcut(item) == "Fn-F"

    def test_a_glyph_key_is_named_from_its_virtual_key(self):
        """Home reports a private-use character that would print as a box,
        but also a vk - and that goes through Keyhac's own name table, so it
        reads the way a key table would spell it."""
        from keyhac.core.vk import init_key_names
        from keyhac.core.sources import _menu_shortcut
        init_key_names("mac")
        item = _FakeMenuElement("AXMenuItem", "x", shortcut={
            "AXMenuItemCmdChar": "", "AXMenuItemCmdVirtualKey": 115,
            "AXMenuItemCmdModifiers": 0})
        assert _menu_shortcut(item) == "Cmd-Home"

    def test_choosing_presses_the_item(self):
        from keyhac.core.sources import MenuItemsSource
        rows = self._rows()
        row = next(c for c in rows if c.display.endswith("Save As…"))
        MenuItemsSource().on_chosen(row, 0)
        assert row.payload.pressed == ["AXPress"]

    def test_the_shortcut_is_the_badge_when_it_is_the_only_source(self):
        from keyhac.core.sources import MenuItemsSource
        row = self._rows()[0]
        assert MenuItemsSource().badge(row) == row.extras["shortcut"]

    def test_a_recursive_menu_does_not_hang_the_walk(self):
        from keyhac.core.sources import _walk_menu
        menu = _menu()
        loop = _item("Loop", kids=[menu])
        menu._kids.append(loop)
        rows = []
        _walk_menu(_FakeMenuElement("AXMenuBar", kids=[loop]), (), rows, 0)
        assert rows == []
