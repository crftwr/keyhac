"""Sources as values, and one window over several of them (discussion #112).

The hotkey is the scarce resource: an action class per kind of row means a key
per kind of row. These cover the shape that lets several kinds share one key -
and the routing that has to come with it, since Enter then means whatever the
chosen row's source says it means.
"""

import time

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
        return list(_walk_menu(self._bar(), (), 0))

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
        assert list(_walk_menu(
            _FakeMenuElement("AXMenuBar", kids=[loop]), (), 0)) == []

    def test_the_menu_bar_is_read_from_the_front_window_not_the_focus(self):
        """The second time this trap has bitten. A `Focus` mixes its sources -
        its window title comes from the AX-focused application, which the
        popup itself can become - and reading the menu bar from there gets
        Keyhac's, which as an accessory app has none. The symptom was a scope
        that came up empty."""
        import keyhac.core.sources as src

        class _Win:
            element = _FakeMenuElement("AXWindow")

        _Win.element.menu_bar = lambda: _FakeMenuElement("AXMenuBar", kids=[
            _FakeMenuElement("AXMenuBarItem", "File",
                             kids=[_menu(_item("New"))])])

        class _Keymap:
            focus = None                      # the route that used to be used
            get_active_window = staticmethod(lambda: _Win())

        original = src.Keymap.get_instance
        src.Keymap.get_instance = staticmethod(lambda: _Keymap())
        try:
            rows = list(src.MenuItemsSource().candidates())
        finally:
            src.Keymap.get_instance = original
        assert [c.display for c in rows] == ["File › New"]

    def test_the_menu_bar_comes_from_element_and_not_from_native(self):
        """`native` is *the platform's own object*: an AX element on macOS,
        but the HWND wrapper on Windows, which has no menu bar to find. That
        is why the Menu scope came up empty on Windows while the same code
        worked on macOS - the tests faked the macOS shape too. `element` is
        the documented bridge from a window to element introspection, and it
        answers an element on both."""
        import keyhac.core.sources as src

        bar = _FakeMenuElement("MenuBar", kids=[
            _FakeMenuElement("MenuItem", "File", kids=[
                _FakeMenuElement("Menu", kids=[
                    _FakeMenuElement("MenuItem", "New")])])])

        class _Win:  # the Windows shape: native is the window itself
            def __init__(self):
                self.element = _FakeMenuElement("Window")
                self.element.menu_bar = lambda: bar

            @property
            def native(self):
                return self

        class _Keymap:
            get_active_window = staticmethod(_Win)

        original = src.Keymap.get_instance
        src.Keymap.get_instance = staticmethod(lambda: _Keymap())
        try:
            rows = list(src.MenuItemsSource().candidates())
        finally:
            src.Keymap.get_instance = original
        assert [c.display for c in rows] == ["File › New"]

    def test_no_front_window_is_an_empty_list_not_a_crash(self):
        import keyhac.core.sources as src

        class _Keymap:
            get_active_window = staticmethod(lambda: None)

        original = src.Keymap.get_instance
        src.Keymap.get_instance = staticmethod(lambda: _Keymap())
        try:
            assert list(src.MenuItemsSource().candidates()) == []
        finally:
            src.Keymap.get_instance = original


class TestKeyBindingsSource:
    """The one source nothing outside Keyhac can offer: the engine's own
    tables, resolved the way the hook resolves them."""

    def _keymap(self, engine, configure):
        fixture = engine(configure)
        fixture.keymap._check_focus_change()
        return fixture.keymap

    def _rows(self, engine, configure):
        from keyhac.core.sources import KeyBindingsSource
        self._keymap(engine, configure)
        return KeyBindingsSource().candidates()

    def test_it_reads_the_table_the_hook_resolves(self, engine):
        """Not a second walk of the configuration - the engine's own merged
        table, so the two cannot drift."""
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            table["Fn-V"] = "Down"

        rows = self._rows(engine, configure)
        assert [(c.display, c.extras["keys"]) for c in rows] == [
            ("Down", "Fn-V")]

    def test_a_table_that_does_not_apply_here_is_absent(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Fn-V"] = "Down"
            keymap.define_keytable(focus_path_pattern="/nowhere/*")["Fn-W"] = "Up"

        assert [c.extras["keys"] for c in self._rows(engine, configure)] == [
            "Fn-V"]

    def test_a_multi_stroke_prefix_is_expanded_to_its_leaves(self, engine):
        """`Fn-X › A` is the sequence you would type, and those are exactly
        the bindings nobody remembers."""
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            sub = keymap.define_keytable(name="sub")
            table["Fn-X"] = sub
            sub["A"] = "Home"
            sub["B"] = "End"

        rows = self._rows(engine, configure)
        assert sorted(c.extras["keys"] for c in rows) == ["Fn-X › A",
                                                          "Fn-X › B"]

    def test_the_prefix_itself_is_not_a_row(self, engine):
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            sub = keymap.define_keytable(name="sub")
            table["Fn-X"] = sub
            sub["A"] = "Home"

        assert "Fn-X" not in [c.extras["keys"] for c in
                              self._rows(engine, configure)]

    def test_a_key_down_loses_its_prefix_but_a_one_shot_keeps_it(self, engine):
        """`D-` is noise in a list where almost everything is a key down;
        `O-` is the unusual thing about the binding."""
        def configure(keymap):
            keymap.define_modifier("RCmd", "User0")
            table = keymap.define_keytable(focus_path_pattern="*")
            table["Fn-V"] = "Down"
            table["O-RCmd"] = "Kana"

        keys = sorted(c.extras["keys"] for c in self._rows(engine, configure))
        assert keys == ["Fn-V", "O-RCmd"]

    def test_what_a_binding_does_reads_in_one_line(self, engine):
        def named():
            pass

        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            table["Fn-A"] = "Home", "Shift-End"
            table["Fn-B"] = named

        by_keys = {c.extras["keys"]: c.display
                   for c in self._rows(engine, configure)}
        assert by_keys["Fn-A"] == "Home Shift-End"
        assert by_keys["Fn-B"] == "named()"

    def test_the_keys_are_searchable_too(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Fn-V"] = "Down"

        row = self._rows(engine, configure)[0]
        assert "Fn-V" in row.match_text and "Down" in row.match_text

    def test_the_keys_are_the_badge(self, engine):
        from keyhac.core.sources import KeyBindingsSource

        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Fn-V"] = "Down"

        row = self._rows(engine, configure)[0]
        assert KeyBindingsSource().badge(row) == "Fn-V"

    def test_choosing_runs_a_callable_binding(self, engine):
        from keyhac.core.sources import KeyBindingsSource
        called = []

        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Fn-V"] = \
                lambda: called.append(1)

        row = self._rows(engine, configure)[0]
        KeyBindingsSource().on_chosen(row, 0)
        assert called == [1]

    def test_choosing_sends_a_key_output_binding(self, engine):
        """A binding you can run from a list is one that does not need a key
        of its own, which is the whole point."""
        from keyhac.core.sources import KeyBindingsSource

        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Fn-V"] = "Down"

        fixture = engine(configure)
        fixture.keymap._check_focus_change()
        row = KeyBindingsSource().candidates()[0]
        fixture.hook.sent.clear()
        KeyBindingsSource().on_chosen(row, 0)
        assert "D-Down" in fixture.sent_names()

    def test_a_recursive_prefix_does_not_hang_the_walk(self, engine):
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            loop = keymap.define_keytable(name="loop")
            table["Fn-X"] = loop
            loop["A"] = loop

        assert self._rows(engine, configure) == []

    def test_a_built_in_action_reads_as_its_name(self, engine):
        """The replay actions had no __repr__ and came out as
        `<keyhac.core.action.ToggleRecordingKeys object at 0x...>`."""
        from keyhac.core.action import ToggleRecordingKeys, PlaybackRecordedKeys

        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            table["Fn-R"] = ToggleRecordingKeys()
            table["Fn-T"] = PlaybackRecordedKeys()

        assert sorted(c.display for c in self._rows(engine, configure)) == [
            "PlaybackRecordedKeys()", "ToggleRecordingKeys()"]

    def test_an_operators_own_class_reads_as_its_name_too(self, engine):
        """The built-ins can be given a __repr__; an operator's class often
        will not have one, and the default repr reads as a failure."""
        from keyhac.core.action import ThreadedAction

        class MyOwn(ThreadedAction):
            def run(self):
                pass

            def finished(self, result):
                pass

        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Fn-M"] = MyOwn()

        assert [c.display for c in self._rows(engine, configure)] == ["MyOwn()"]

    def test_a_real_repr_still_wins(self, engine):
        def configure(keymap):
            from keyhac.actions import DateTimeSnippet
            keymap.define_keytable(focus_path_pattern="*")["Fn-D"] = \
                DateTimeSnippet("%Y-%m-%d")

        assert [c.display for c in self._rows(engine, configure)] == [
            "DateTimeSnippet('%Y-%m-%d')"]


class TestCandidateRowLayout:
    """The row widget's own geometry. `ListView` hands a row `ctx.width - 1`
    when a scrollbar is showing, so the row's right edge *is* the scrollbar's
    left edge and anything drawn flush there touches it."""

    def _render(self, width=46, rows=12):
        from puikit import Panel
        from puikit.backends.memory_backend import MemoryBackend
        from puikit.widgets.list import ListView
        from keyhac.ui.candidate_row import CandidateRow

        backend = MemoryBackend(width, 5)
        backend.open()
        items = [(f"entry number {i}", "Clipboard") for i in range(rows)]
        lst = ListView(items, row_factory=lambda r: CandidateRow(*r),
                       allow_no_selection=True)
        panel = Panel(backend)
        panel.add(lst, 0, 0, width, 4)
        panel.render()
        return ["".join(row) for row in backend.snapshot()]

    def test_the_badge_does_not_touch_the_scrollbar(self):
        lines = self._render()
        assert any("▅" in line for line in lines), "no scrollbar to sit beside"
        for line in lines:
            if "Clipboard" not in line:
                continue
            after = line[line.index("Clipboard") + len("Clipboard"):]
            assert after.startswith(" "), f"badge is flush: {line!r}"

    def test_the_badge_is_still_right_aligned(self):
        lines = self._render()
        badged = [l for l in lines if "Clipboard" in l]
        assert badged
        # One blank column, then the scrollbar column: the badge ends two
        # short of the pane, not further in.
        for line in badged:
            assert line.rstrip().endswith("Clipboard") or \
                line.index("Clipboard") + len("Clipboard") >= len(line) - 2

    def test_the_insets_are_pixels_on_a_vector_backend_and_a_column_on_a_grid(
            self):
        """A whole column would be a gulf where pixels are available, and a
        grid cannot express less than one."""
        from keyhac.ui.candidate_row import (
            CandidateRow, _LEADING_PX, _TRAILING_PX)

        class _Ctx:
            def __init__(self, vector, base_w):
                self.vector_shapes = vector
                self.base_size = (base_w, 1)

        row = CandidateRow("x", "y")
        for px in (_LEADING_PX, _TRAILING_PX):
            assert row._inset(_Ctx(False, 8), px) == 1.0
            assert row._inset(_Ctx(True, 8), px) == px / 8
            assert row._inset(_Ctx(True, 0), px) == 0.0

    def test_the_icon_is_not_flush_against_the_window(self):
        """The list runs to the page's edge now, with no frame of its own in
        between, so a row starting at column zero starts against the window."""
        lines = self._render()
        assert lines and all(line.startswith(" ") for line in lines if line.strip())


class TestActionsSource:
    """Everything in `extensions/`, startable without a key - the half of the
    authoring loop a key binding never covered."""

    ACTION = """
        from keyhac.core.action import ThreadedAction

        class {name}(ThreadedAction):
            \"\"\"{doc}\"\"\"
            {init}
            def run(self):
                {body}
            def finished(self, result):
                pass
    """

    def _write(self, keymap, path, name, doc, init="", body="pass"):
        import os
        import textwrap
        full = os.path.join(keymap.extensions_dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        init_dir = os.path.dirname(full)
        if init_dir != keymap.extensions_dir:
            open(os.path.join(init_dir, "__init__.py"), "a").close()
        with open(full, "w") as handle:
            handle.write(textwrap.dedent(
                self.ACTION.format(name=name, doc=doc, init=init, body=body)))

    def _source(self, engine, write):
        import os
        from keyhac.core.sources import ActionsSource
        fixture = engine(lambda keymap: keymap.define_keytable(
            focus_path_pattern="*"))
        os.makedirs(fixture.keymap.extensions_dir, exist_ok=True)
        write(fixture.keymap)
        return ActionsSource(), fixture.keymap

    def test_an_action_reads_as_its_docstring(self, engine):
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "translate.py", "Translate", "Translate the clipboard."))
        rows = source.candidates()
        assert [c.display for c in rows] == ["Translate the clipboard."]

    def test_the_badge_is_how_it_is_addressed(self, engine):
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "translate.py", "Translate", "Translate the clipboard."))
        assert source.badge(source.candidates()[0]) == "translate.Translate"

    def test_an_action_in_a_subdirectory_is_listed(self, engine):
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "mine/extract.py", "Extract", "Pull the table out."))
        assert source.badge(source.candidates()[0]) == "mine.extract.Extract"

    def test_both_the_address_and_the_summary_are_searchable(self, engine):
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "translate.py", "Translate", "Translate the clipboard."))
        text = source.candidates()[0].match_text
        assert "translate.Translate" in text and "clipboard" in text

    def test_a_class_that_is_only_callable_is_not_offered(self, engine):
        """It binds to a key perfectly well. The main thread services the hook
        and every window, so a list whose rows might block it is a list that
        can freeze the keyboard."""
        import os

        def write(keymap):
            with open(os.path.join(keymap.extensions_dir, "fast.py"), "w") as h:
                h.write('class Fast:\n    """Callable, not threaded."""\n'
                        "    def __call__(self):\n        pass\n")

        source, _keymap = self._source(engine, write)
        assert source.candidates() == []

    def test_one_needing_arguments_is_listed_and_says_so(self, engine):
        """Hiding it would read as Keyhac not seeing the file, which is a much
        worse thing to debug than a row that explains itself."""
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "deploy.py", "Deploy", "Deploy somewhere.",
            init="def __init__(self, environment):\n"
                 "                self.environment = environment"))
        row = source.candidates()[0]
        assert source.badge(row) == "needs environment"

    def test_choosing_one_that_needs_arguments_explains_rather_than_raises(
            self, engine, caplog):
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "deploy.py", "Deploy", "Deploy somewhere.",
            init="def __init__(self, environment):\n"
                 "                self.environment = environment"))
        with caplog.at_level("ERROR"):
            source.on_chosen(source.candidates()[0], 0)
        assert "environment" in caplog.text

    def test_listing_does_not_import(self, engine):
        """The property the directory has always had: a module no config.py
        imports is inert on disk."""
        import sys
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "boom.py", "Boom", "Would raise on import.",
            body="pass"))
        before = set(sys.modules)
        source.candidates()
        assert "boom" not in set(sys.modules) - before

    def test_choosing_runs_it(self, engine, tmp_path):
        marker = tmp_path / "ran"
        source, _keymap = self._source(engine, lambda km: self._write(
            km, "touch.py", "Touch", "Leaves a marker.",
            body=f"open({str(marker)!r}, 'w').close()"))
        from keyhac.core.keymap import Keymap
        Keymap._prepare_extensions(_keymap.extensions_dir)
        source.on_chosen(source.candidates()[0], 0)
        # run() is on the shared worker, so the marker appears after this
        # returns rather than during it.
        deadline = time.time() + 5
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert marker.exists()

    def test_no_extensions_directory_is_an_empty_list(self, engine):
        from keyhac.core.sources import ActionsSource
        engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        assert ActionsSource().candidates() == []


class TestStreaming:
    """A source with real work to do yields, and the window drains it a slice
    at a time between renders - so its first rows are on screen while it is
    still finding the rest."""

    class _Slow(CandidateSource):
        name = "Slow"

        def __init__(self, count=6):
            self.count = count
            self.produced = 0
            self.finished = False
            self.chosen = []

        def candidates(self):
            for index in range(self.count):
                self.produced += 1
                yield Candidate(display=f"row {index}")
            self.finished = True

        def on_chosen(self, candidate, modifier_flags):
            self.chosen.append(candidate.display)

    def _open(self, sources, ui_backend=None):
        from keyhac.actions import ChooserAction, ShowCandidates
        action = ShowCandidates(sources)
        action()
        chooser = ChooserAction._open[1]
        if ui_backend is not None:
            self._pump(ui_backend)
        return action, chooser

    @staticmethod
    def _pump(backend, frames=40):
        """Drive the animation ticks the real event loop would.

        The pump is registered, not run, by opening the window - so a test
        that wants the streamed rows has to turn the handle, which is also
        what proves they arrive through the pump rather than from the
        constructor's argument list.
        """
        for _ in range(frames):
            backend.run_animation_ticks()

    def test_a_list_source_does_not_stream_at_all(self, ui_backend):
        """Nothing is gained by deferring rows already in hand, and much is
        lost in making every caller wait for them."""
        _action, chooser = self._open(_Fruit())
        assert chooser._pending is None
        assert len(chooser._items) == 2

    def test_a_yielding_source_arrives_through_the_pump(self, ui_backend):
        source = self._Slow()
        _action, chooser = self._open(source)
        assert chooser._items == [], "nothing should arrive before a tick"
        self._pump(ui_backend)
        assert [c.display for c in chooser._items] == [
            f"row {i}" for i in range(6)]

    def test_the_window_opens_before_the_rows_are_all_known(self, ui_backend):
        """The point of the exercise: a slice at a time, not one long wait."""
        from keyhac.ui.chooser import ChooserWindow
        seen = []

        def produce():
            for index in range(4):
                seen.append(index)
                yield Candidate(display=f"row {index}")

        chooser = ChooserWindow(ui_backend, [], pending=produce())
        assert seen == [], "the window is up before the source has been read"
        self._pump(ui_backend)
        assert seen == [0, 1, 2, 3]
        assert len(chooser._items) == 4
        chooser.dismiss()

    def test_appending_keeps_the_selection_and_the_scroll(self, ui_backend):
        """Appending never reorders: rows already passing the filter keep
        their indices, so a list still filling does not move under the hand
        choosing from it. A changed query resets both, deliberately."""
        from keyhac.ui.chooser import ChooserWindow

        rows = [Candidate(display=f"row {i}") for i in range(60)]
        chooser = ChooserWindow(ui_backend, rows)
        chooser._list.selected = 30
        chooser.panel.render()
        offset = chooser._list.offset
        assert offset > 0, "the list has to be scrolled for this to mean anything"
        chooser._append([Candidate(display="row 60")])
        assert chooser._list.selected == 30
        assert chooser._list.offset == offset
        assert [c.display for c in chooser._filtered][-1] == "row 60"
        chooser.dismiss()

    def test_an_arriving_row_that_does_not_match_is_not_shown(self, ui_backend):
        from keyhac.ui.chooser import ChooserWindow

        chooser = ChooserWindow(ui_backend, [Candidate(display="alpha")])
        chooser._edit.text = "al"
        chooser._on_filter_change("al")
        chooser._append([Candidate(display="zulu"), Candidate(display="also")])
        shown = [c.display for c in chooser._filtered]
        # Ranked, not appended in arrival order: both start with the query, so
        # the shorter one wins.
        assert shown == ["also", "alpha"]
        assert len(chooser._items) == 3, "it is still a candidate, just filtered"
        chooser.dismiss()

    def test_switching_scope_abandons_what_the_last_one_was_producing(
            self, ui_backend):
        """By dropping the iterator - nothing has to be told to stop."""
        from keyhac.core.source import Scope
        from puikit.event import Event, EventType
        source = self._Slow(count=200)
        _action, chooser = self._open([Scope("Slow", [source]),
                                       Scope("Fruit", [_Fruit()])])
        # Deliberately without pumping: opening registers the drain, it does
        # not run it, so the switch lands while the source is untouched.
        assert source.produced == 0
        chooser._on_event(Event(type=EventType.KEY, key="tab"))
        assert chooser._pending is None
        assert [c.display for c in chooser._items] == ["apple", "apricot"]
        # And it stays abandoned: the ticks that follow belong to the scope
        # now showing, and the old generator is simply never asked again.
        self._pump(ui_backend)
        assert source.produced == 0 and source.finished is False

    def test_choosing_a_streamed_row_still_reaches_its_source(self, ui_backend):
        source = self._Slow()
        _action, chooser = self._open(source, ui_backend)
        chooser._finish(chooser._filtered[2], 0)
        assert source.chosen == ["row 2"]

    def test_a_source_that_yields_nothing_is_not_an_error(self, ui_backend):
        class _Empty(CandidateSource):
            def candidates(self):
                return iter(())

        _action, chooser = self._open(_Empty(), ui_backend)
        assert chooser._items == []


class TestRanking:
    """A window merging several sources cannot show them concatenated: a
    thousand clipboard entries would bury every menu command behind them."""

    def _chooser(self, ui_backend, displays):
        from keyhac.ui.chooser import ChooserWindow
        return ChooserWindow(
            ui_backend, [Candidate(display=d) for d in displays])

    def _shown(self, chooser, query):
        chooser._edit.text = query
        chooser._on_filter_change(query)
        return [c.display for c in chooser._filtered]

    def test_a_prefix_beats_a_word_start_beats_the_middle(self, ui_backend):
        chooser = self._chooser(ui_backend, [
            "autosaved backup",          # inside a word
            "File › Save As…",           # starts a word
            "Save As…",                  # starts the text
        ])
        assert self._shown(chooser, "save") == [
            "Save As…", "File › Save As…", "autosaved backup"]
        chooser.dismiss()

    def test_an_earlier_match_beats_a_later_one(self, ui_backend):
        chooser = self._chooser(ui_backend, [
            "a very long clipboard entry that mentions save at the end",
            "File › Save As…",
        ])
        assert self._shown(chooser, "save")[0] == "File › Save As…"
        chooser.dismiss()

    def test_a_shorter_row_wins_an_otherwise_equal_match(self, ui_backend):
        chooser = self._chooser(ui_backend, ["saved搜索 nonsense", "save"])
        assert self._shown(chooser, "save")[0] == "save"
        chooser.dismiss()

    def test_an_empty_query_keeps_the_order_the_sources_produced(
            self, ui_backend):
        """Clipboard history newest first, and so on. There is no match to
        judge the quality of."""
        order = ["third", "second", "first"]
        chooser = self._chooser(ui_backend, order)
        assert self._shown(chooser, "") == order
        chooser.dismiss()

    def test_rows_the_query_cannot_tell_apart_keep_their_order(self,
                                                               ui_backend):
        chooser = self._chooser(ui_backend, ["save one", "save two",
                                             "save three"])
        assert self._shown(chooser, "save") == ["save one", "save two",
                                                "save three"]
        chooser.dismiss()

    def test_a_streamed_row_is_ranked_into_place_not_appended(self,
                                                              ui_backend):
        """The point of ranking while streaming: a late arrival that is the
        best match must not sit at the bottom."""
        chooser = self._chooser(ui_backend, ["autosaved backup"])
        self._shown(chooser, "save")
        chooser._append([Candidate(display="Save As…")])
        assert [c.display for c in chooser._filtered][0] == "Save As…"
        chooser.dismiss()

    def test_the_row_under_the_selection_does_not_move(self, ui_backend):
        """Ranking wants to reorder and a list being chosen from must not
        shift, so what is kept is the candidate, not its index."""
        chooser = self._chooser(ui_backend, ["autosaved one", "autosaved two"])
        self._shown(chooser, "save")
        chooser._list.selected = 1
        standing_on = chooser._filtered[1]
        chooser._append([Candidate(display="Save As…")])   # ranks to the top
        assert chooser._filtered[0].display == "Save As…"
        assert chooser._filtered[chooser._list.selected] is standing_on
        chooser.dismiss()

    def test_nothing_is_selected_while_the_field_has_the_focus(self,
                                                              ui_backend):
        """Why the two rules do not actually conflict: rows arrive during
        exactly the window in which no row is selected."""
        chooser = self._chooser(ui_backend, ["alpha"])
        assert chooser._list.selected == -1
        chooser._append([Candidate(display="beta")])
        assert chooser._list.selected == -1
        chooser.dismiss()

    def test_the_query_is_compiled_once_per_change_not_once_per_slice(
            self, ui_backend):
        """Migemo's whole cost is building the regex; paying it on every
        frame of a streaming source would be paying it dozens of times."""
        from keyhac.core.matcher import SubstringMatcher
        from keyhac.ui.chooser import ChooserWindow

        compiles = []

        class _Counting(SubstringMatcher):
            def compile(self, query):
                compiles.append(query)
                return super().compile(query)

        chooser = ChooserWindow(ui_backend, [Candidate(display="alpha")],
                                matcher=_Counting())
        chooser._on_filter_change("a")
        before = len(compiles)
        for _ in range(5):
            chooser._append([Candidate(display="another")])
        assert len(compiles) == before
        chooser.dismiss()


class _FakeControl:
    """An element in the shape both platforms' describe() produces."""

    def __init__(self, role, name=None, name_source=None, rect=None,
                 kids=(), key=None):
        self._described = {"role": role, "name": name,
                           "name_source": name_source, "rect": rect}
        self._kids = list(kids)
        self._key = key
        self.described = 0
        self.pressed = []

    def describe(self):
        self.described += 1
        return dict(self._described)

    def role(self):
        return self._described["role"]

    def children(self):
        return self._kids

    def identity_key(self):
        return self._key

    def perform_action(self, action):
        self.pressed.append(action)
        return action == "AXPress"


class TestWindowControlsSource:
    """Discussion #112's original target, and the reason the window had to
    stop taking the keyboard focus."""

    def _rows(self, root):
        from keyhac.core.sources import _walk_controls
        return list(_walk_controls(root))

    def test_a_named_control_is_offered(self):
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXButton", "Save", name_source="label")])
        assert [c.display for c in self._rows(root)] == ["Save"]

    def test_content_is_not_a_control(self):
        """The measured tree of a heavy application is overwhelmingly groups
        and static text - the page, not the buttons on it."""
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXStaticText", "Some prose"),
            _FakeControl("AXGroup", "A group", kids=[
                _FakeControl("AXButton", "Inside")]),
        ])
        assert [c.display for c in self._rows(root)] == ["Inside"]

    def test_an_unnamed_control_is_not_offered(self):
        """There is no text to filter on, so the row would be one nobody can
        reach."""
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXButton", None),
            _FakeControl("AXButton", "")])
        assert self._rows(root) == []

    def test_where_the_name_came_from_travels_with_the_row(self):
        """It decides what else can find the element: one reachable only
        through its tooltip cannot be found by find(name=...) either."""
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXButton", "Bold", name_source="description")])
        assert self._rows(root)[0].provenance == "description"

    def test_the_screen_rectangle_travels_too(self):
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXButton", "Save", rect=(10, 20, 30, 40))])
        assert self._rows(root)[0].rect == (10, 20, 30, 40)

    def test_the_role_is_the_badge(self):
        from keyhac.core.sources import WindowControlsSource
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXCheckBox", "Wrap")])
        row = self._rows(root)[0]
        assert WindowControlsSource().badge(row) == "AXCheckBox"

    def test_a_menu_item_is_a_control_like_any_other(self):
        """How a Windows menu reaches the user at all. There is no menu scope
        there - the bar is not an OS-level part and fills only when it opens -
        so the window's own top-level items are listed here, and choosing one
        opens that menu, which is what clicking it does."""
        root = _FakeControl("Window", kids=[
            _FakeControl("MenuBar", "Application", kids=[
                _FakeControl("MenuItem", "File"),
                _FakeControl("MenuItem", "Edit")])])
        assert [c.display for c in self._rows(root)] == ["File", "Edit"]

    def test_an_element_reached_twice_is_reported_once(self):
        """A table's cells are children of their row *and* of their column, so
        without the dedupe every cell of every table appears twice."""
        cell = _FakeControl("AXButton", "Cell", key="cell-1")
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXGroup", "row", kids=[cell]),
            _FakeControl("AXGroup", "column", kids=[cell]),
        ])
        assert [c.display for c in self._rows(root)] == ["Cell"]

    def test_the_tree_comes_from_element_and_not_from_native(self):
        """The Control scope's half of the same bug: on Windows `native` is
        the HWND wrapper, which has no children to walk, so the scope came up
        empty. See TestMenuItemsSource's twin."""
        import keyhac.core.sources as src

        tree = _FakeControl("Window", kids=[_FakeControl("Button", "Save")])

        class _Win:  # the Windows shape: native is the window itself
            element = tree

            @property
            def native(self):
                return self

        class _Keymap:
            get_active_window = staticmethod(_Win)

        original = src.Keymap.get_instance
        src.Keymap.get_instance = staticmethod(lambda: _Keymap())
        try:
            rows = list(src.WindowControlsSource().candidates())
        finally:
            src.Keymap.get_instance = original
        assert [c.display for c in rows] == ["Save"]

    def test_a_platform_without_identities_is_not_deduped_away(self):
        """UI Automation's control view is a real tree and returns no key;
        two distinct buttons must not collapse into one."""
        root = _FakeControl("Window", kids=[
            _FakeControl("Button", "One"), _FakeControl("Button", "Two")])
        assert [c.display for c in self._rows(root)] == ["One", "Two"]

    def test_it_yields_before_it_has_finished_walking(self):
        """The whole reason it can be used at all: a heavy application takes
        hundreds of milliseconds, and the first controls should not wait."""
        from keyhac.core.sources import _walk_controls
        deep = _FakeControl("AXButton", "Last")
        for _ in range(20):
            deep = _FakeControl("AXGroup", "g", kids=[deep])
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXButton", "First"), deep])
        walk = _walk_controls(root)
        assert next(walk).display == "First"

    def test_the_walk_is_bounded(self):
        from keyhac.core import sources
        root = _FakeControl("AXWindow", kids=[
            _FakeControl("AXButton", f"Button {i}") for i in range(50)])
        original = sources._CONTROLS_MAX_NODES
        sources._CONTROLS_MAX_NODES = 10
        try:
            assert len(self._rows(root)) < 50
        finally:
            sources._CONTROLS_MAX_NODES = original

    def test_choosing_presses_it(self):
        from keyhac.core.sources import WindowControlsSource
        button = _FakeControl("AXButton", "Save")
        row = self._rows(_FakeControl("AXWindow", kids=[button]))[0]
        WindowControlsSource().on_chosen(row, 0)
        assert button.pressed == ["AXPress"]

    def test_no_front_window_is_an_empty_list(self, ui_backend):
        import keyhac.core.sources as src

        class _Keymap:
            get_active_window = staticmethod(lambda: None)

        original = src.Keymap.get_instance
        src.Keymap.get_instance = staticmethod(lambda: _Keymap())
        try:
            assert list(src.WindowControlsSource().candidates()) == []
        finally:
            src.Keymap.get_instance = original

    def test_only_the_elements_worth_reporting_are_described(self):
        """describe() is a batched read of nine attributes, and most of a
        window's tree is content this will never report. Reading the role
        first and describing only what matters took VS Code's 4000-node walk
        from 588 ms to 346."""
        passed_through = _FakeControl("AXGroup", "a group")
        button = _FakeControl("AXButton", "Save", name_source="label")
        root = _FakeControl("AXWindow", kids=[passed_through, button])
        self._rows(root)
        assert passed_through.described == 0
        assert button.described == 1


class TestScopeCaching:
    """Tabbing between scopes used to re-read each one. What makes keeping
    them safe is not a guess about staleness: the dismissal watch closes the
    window the moment the front window changes, so nothing a scope read can
    have gone stale while the window is still up."""

    class _Counting(CandidateSource):
        def __init__(self, name, rows):
            self.name = name
            self.rows = rows
            self.reads = 0

        def candidates(self):
            self.reads += 1
            return [Candidate(display=r) for r in self.rows]

    def _open(self, scopes):
        from keyhac.actions import ChooserAction, ShowCandidates
        action = ShowCandidates(scopes)
        action()
        return action, ChooserAction._open[1]

    def _scoped(self, *sources):
        from keyhac.core.source import Scope
        return [Scope(s.name, [s]) for s in sources]

    def _tab(self, chooser, shift=False):
        from puikit.event import Event, EventType
        chooser._on_event(Event(type=EventType.KEY, key="tab",
                                modifiers=frozenset({"shift"} if shift else ())))

    def test_a_scope_is_read_once_per_window(self, ui_backend):
        slow = self._Counting("Slow", ["a", "b"])
        other = self._Counting("Other", ["c"])
        _action, chooser = self._open(self._scoped(slow, other))
        assert slow.reads == 1
        self._tab(chooser)                      # to Other
        self._tab(chooser)                      # back to Slow
        assert slow.reads == 1, "tabbing back re-read it"
        assert [c.display for c in chooser._items] == ["a", "b"]

    def test_reopening_the_window_does_read_again(self, ui_backend):
        """The cache is the window's, not the process's - a new window is a
        new question about a screen that has had time to move."""
        source = self._Counting("Slow", ["a"])
        action, _chooser = self._open(
            self._scoped(source, self._Counting("Other", ["c"])))
        action()                                # closes
        action()                                # opens again
        assert source.reads == 2

    def test_a_half_read_scope_resumes_rather_than_restarting(self,
                                                              ui_backend):
        from keyhac.core.source import Scope
        from keyhac.actions import ChooserAction, ShowCandidates

        produced = []

        class _Streaming(CandidateSource):
            name = "Streaming"

            def candidates(self):
                for index in range(6):
                    produced.append(index)
                    yield Candidate(display=f"row {index}")

        action = ShowCandidates([Scope("Streaming", [_Streaming()]),
                                 Scope("Other", [_Fruit()])])
        action()
        chooser = ChooserAction._open[1]
        ui_backend.run_animation_ticks()
        seen = len(produced)
        assert seen > 0
        self._tab(chooser)
        self._tab(chooser, shift=True)          # back
        for _ in range(40):
            ui_backend.run_animation_ticks()
        assert produced == list(range(6)), "the generator restarted"

    def test_a_source_shared_between_scopes_is_read_once(self, ui_backend):
        """The everything-scope and a scope of its own should not walk the
        same menu bar twice for one press of one key."""
        from keyhac.core.source import Scope
        shared = self._Counting("Shared", ["a", "b"])
        _action, chooser = self._open([
            Scope("All", [shared, self._Counting("Other", ["c"])]),
            Scope("Just it", [shared]),
        ])
        assert shared.reads == 1
        self._tab(chooser)
        assert shared.reads == 1
        assert [c.display for c in chooser._items] == ["a", "b"]

    def test_two_separately_built_sources_are_two_sources(self, ui_backend):
        """Which is the right answer when they differ - two SnippetsSource
        with different snippets are not interchangeable."""
        from keyhac.core.source import Scope
        one = self._Counting("One", ["a"])
        two = self._Counting("Two", ["b"])
        _action, chooser = self._open([Scope("First", [one]),
                                       Scope("Second", [two])])
        self._tab(chooser)
        assert (one.reads, two.reads) == (1, 1)
        assert [c.display for c in chooser._items] == ["b"]

    def test_a_shared_streaming_source_carries_its_progress_across(self,
                                                                   ui_backend):
        """A row read in one scope starts the other from where this one got
        to, rather than from nothing."""
        from keyhac.core.source import Scope
        from keyhac.actions import ChooserAction, ShowCandidates

        produced = []

        class _Streaming(CandidateSource):
            name = "Streaming"

            def candidates(self):
                for index in range(6):
                    produced.append(index)
                    yield Candidate(display=f"row {index}")

        shared = _Streaming()
        ShowCandidates([Scope("All", [shared]), Scope("Just it", [shared])])()
        chooser = ChooserAction._open[1]
        for _ in range(40):
            ui_backend.run_animation_ticks()
        assert produced == list(range(6))
        self._tab(chooser)
        for _ in range(40):
            ui_backend.run_animation_ticks()
        assert produced == list(range(6)), "the shared source was read again"
        assert len(chooser._items) == 6

class TestProgress:
    """Without a sign that a list is still filling, a query that has not
    matched *yet* reads as one that never will."""

    def _open(self, produce):
        from keyhac.actions import ChooserAction, ShowCandidates
        action = ShowCandidates(produce)
        action()
        return ChooserAction._open[1]

    def test_it_says_how_far_it_has_got_while_reading(self, ui_backend):
        def produce():
            for index in range(10):
                # Enough per row that one slice cannot swallow the lot, which
                # is the state this note exists to describe.
                time.sleep(0.001)
                yield Candidate(display=f"row {index}")

        chooser = self._open(produce)
        ui_backend.run_animation_ticks()
        assert chooser._pending is not None, "it finished in one slice"
        # stripped: the note carries its own leading gap from the field
        assert chooser._progress.text.strip().startswith("…")
        assert chooser._progress.text.split()[-1] == str(len(chooser._items))

    def test_it_goes_quiet_when_there_is_nothing_left(self, ui_backend):
        def produce():
            yield Candidate(display="only")

        chooser = self._open(produce)
        for _ in range(10):
            ui_backend.run_animation_ticks()
        assert chooser._progress.text == ""

    def test_a_list_source_never_says_anything(self, ui_backend):
        chooser = self._open(lambda: [Candidate(display="a")])
        assert chooser._progress.text == ""


class TestCallableShape:
    """A bare callable keeps the shape it produced. Materialising a generator
    here would throw its streaming away silently, and turning a list into one
    would make every list source stream for nothing."""

    def test_a_callable_that_yields_streams(self, ui_backend):
        from keyhac.actions import ChooserAction, ShowCandidates

        def produce():
            yield Candidate(display="a")
            yield Candidate(display="b")

        ShowCandidates(produce)()
        chooser = ChooserAction._open[1]
        assert chooser._pending is not None
        assert chooser._items == []
        ui_backend.run_animation_ticks()
        assert [c.display for c in chooser._items] == ["a", "b"]

    def test_a_callable_that_returns_a_list_does_not(self, ui_backend):
        from keyhac.actions import ChooserAction, ShowCandidates
        ShowCandidates(lambda: [Candidate(display="a")])()
        chooser = ChooserAction._open[1]
        assert chooser._pending is None
        assert [c.display for c in chooser._items] == ["a"]




class TestPointingAtTheSelection:
    """Discussion #112's argument for `node.highlight()`: a row that cannot
    describe itself - an icon-only control listed by its tooltip - is a row
    whose text does not settle *which* one it is. Lighting the real one up is
    the only confirmation available."""

    class _Marks:
        """Stands in for the backend's screen marks."""

        def __init__(self):
            self.made = []

        def mark_screen(self, x, y, w=None, h=None, **kwargs):
            mark = _FakeMark(x, y, w, h)
            self.made.append(mark)
            return mark

    def _chooser(self, ui_backend, rows):
        from keyhac.ui.chooser import ChooserWindow
        marks = self._Marks()
        chooser = ChooserWindow(ui_backend, rows)
        chooser._backend = marks
        return chooser, marks

    def _rows(self):
        return [Candidate(display="Save", rect=(10, 20, 30, 40)),
                Candidate(display="Open", rect=(50, 60, 70, 80))]

    def test_stepping_into_the_list_points_at_the_first_row(self, ui_backend):
        chooser, marks = self._chooser(ui_backend, self._rows())
        chooser._navigate("down")
        assert [(m.x, m.y, m.w, m.h) for m in marks.made] == [(10, 20, 30, 40)]
        assert not marks.made[0].closed
        chooser.dismiss()

    def test_moving_the_selection_moves_the_mark(self, ui_backend):
        chooser, marks = self._chooser(ui_backend, self._rows())
        chooser._navigate("down")
        chooser._navigate("down")
        assert len(marks.made) == 2
        assert marks.made[0].closed, "the previous outline should be gone"
        assert (marks.made[1].x, marks.made[1].y) == (50, 60)
        chooser.dismiss()

    def test_jumping_to_the_last_row_moves_the_mark_with_it(self, ui_backend):
        """Every move of the selection goes through the window's own
        _navigate, so the outline follows. Letting the ListView answer
        Home/End itself moved the highlight and left the mark on the row the
        selection had just left."""
        from puikit.event import Event, EventType
        chooser, marks = self._chooser(ui_backend, self._rows())
        chooser._navigate("down")
        chooser._on_event(Event(type=EventType.KEY, key="end"))
        assert chooser._list.selected == 1
        assert (marks.made[-1].x, marks.made[-1].y) == (50, 60)
        chooser.dismiss()

    def test_leaving_the_list_stops_pointing(self, ui_backend):
        chooser, marks = self._chooser(ui_backend, self._rows())
        chooser._navigate("down")
        chooser._focus_edit()
        assert marks.made[0].closed
        assert chooser._pointer is None
        chooser.dismiss()

    def test_a_row_with_no_place_on_screen_points_at_nothing(self, ui_backend):
        """A clipboard entry is not anywhere."""
        chooser, marks = self._chooser(
            ui_backend, [Candidate(display="some copied text")])
        chooser._navigate("down")
        assert marks.made == []
        chooser.dismiss()

    def test_an_unchanged_selection_redraws_nothing(self, ui_backend):
        chooser, marks = self._chooser(ui_backend, self._rows())
        chooser._navigate("down")
        chooser._point_at_selection()
        chooser._point_at_selection()
        assert len(marks.made) == 1

    def test_closing_the_window_takes_the_mark_with_it(self, ui_backend):
        chooser, marks = self._chooser(ui_backend, self._rows())
        chooser._navigate("down")
        chooser._finish(chooser._filtered[0], 0)
        assert marks.made[0].closed

    def test_a_platform_that_cannot_mark_is_not_an_error(self, ui_backend):
        class _Refuses:
            def mark_screen(self, *a, **k):
                raise RuntimeError("no marks here")

        from keyhac.ui.chooser import ChooserWindow
        chooser = ChooserWindow(ui_backend, self._rows())
        chooser._backend = _Refuses()
        chooser._navigate("down")
        assert chooser._pointer is None
        chooser.dismiss()


class _FakeMark:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.closed = False

    def close(self):
        self.closed = True


class TestPressingAChosenElement:
    """`_press`: the one "press this" both element sources use."""

    class _Element:
        def __init__(self, offers, presses=(), focusable=False):
            self._offers = offers
            self._presses = presses
            self._focusable = focusable
            self.tried = []
            self.focused = False

        def get_action_names(self):
            if self._offers is None:
                raise AttributeError("this platform does not say")
            return list(self._offers)

        def perform_action(self, name):
            self.tried.append(name)
            return name in self._presses

        def set_focus(self):
            self.focused = self._focusable
            return self._focusable

    def test_only_the_actions_the_element_offers_are_tried(self):
        """A name from the other platform is not a miss to try past - it is
        one this platform has never heard of, and Windows logs a warning for
        each. Probing blind put "Unknown UI Automation action: 'AXPress'" in
        the console on every press there."""
        from keyhac.core.sources import _press
        element = self._Element(["Invoke", "Expand"], presses=["Invoke"])
        assert _press(element)
        assert element.tried == ["Invoke"], "AXPress is not a Windows action"

    def test_an_element_that_cannot_say_is_probed_in_order(self):
        from keyhac.core.sources import _press
        element = self._Element(None, presses=["Invoke"])
        assert _press(element)
        assert element.tried == ["AXPress", "Invoke"]

    def test_an_element_nothing_presses_says_so(self):
        from keyhac.core.sources import _press
        assert not _press(self._Element(["Invoke"]))

    @pytest.mark.parametrize("offers,expected", [
        (["Select", "Expand", "Collapse"], "Select"),   # a tab, a tree row
        (["Toggle"], "Toggle"),                          # a toggle button
        (["Expand", "Collapse"], "Expand"),              # a menu item, a header
        (["Invoke", "Select"], "Invoke"),                # run it before selecting
    ])
    def test_what_a_click_would_do_decides_the_order(self, offers, expected):
        """macOS says all of this with one word (AXPress); UIA gives each its
        own pattern. A list of only Invoke refused every tab in VS Code's
        activity bar - "Extensions (Ctrl+Shift+X)" was one of them."""
        from keyhac.core.sources import _press
        element = self._Element(offers, presses=offers)
        assert _press(element)
        assert element.tried == [expected]

    def test_a_row_with_no_press_pattern_is_focused_instead(self):
        """A text field has none - and clicking one is how the caret gets into
        it, so focusing it *is* pressing it. Chromium's list items have none
        either (26 of them in VS Code)."""
        from keyhac.core.sources import _press
        element = self._Element([], focusable=True)
        assert _press(element)
        assert element.focused

    def test_a_row_that_cannot_even_be_focused_says_no(self):
        from keyhac.core.sources import _press
        element = self._Element([], focusable=False)
        assert not _press(element)
