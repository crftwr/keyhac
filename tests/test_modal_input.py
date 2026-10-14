"""A candidate window driven entirely from the key hook (discussion #112).

The load-bearing question in that thread is whether the candidate window can
stop taking OS keyboard focus - a list of "what is actionable in the current
window" changes the current window by opening, and a list of "which bindings
apply to the focused element" contradicts itself.  A non-activating window
gets no keystrokes from the OS, so they have to arrive through the hook that
is already installed.

These drive real hook events through the Keymap and assert what reaches the
window, so the whole route is exercised: hook -> modal grab -> vk translation
-> Panel -> widget.
"""

import pytest

from puikit.event import EventType

from keyhac.core.const import MODKEY_SHIFT, MODKEY_SHIFT_L, MODKEY_SHIFT_R
from keyhac.core.key import KeyCondition
from keyhac.core.vk import get_key_names
from keyhac.ui import runtime
from keyhac.ui.chooser import ChooserWindow
from keyhac.ui.keyroute import to_event


@pytest.fixture
def ui_backend(engine):
    def configure(keymap):
        keymap.define_keytable(focus_path_pattern="*")

    fixture = engine(configure)
    from puikit.backends.memory_backend import MemoryBackend
    backend = MemoryBackend(width=100, height=30)
    backend.open()
    runtime.backend = backend
    yield fixture, backend
    runtime.backend = None
    fixture.keymap.pop_modal_input()
    backend.close()


def _key(name: str, mod: int = 0) -> KeyCondition:
    return KeyCondition(get_key_names().str_to_vk(name), mod)


class TestKeyRoute:
    """vk + modifier -> the Event shape puikit's keyboard contract fixes."""

    def test_letter_is_lowercase_key_with_the_typed_glyph(self, engine):
        engine(lambda keymap: None)
        event = to_event(_key("A"))
        assert (event.key, event.char, event.modifiers) == ("a", "a", frozenset())

    def test_shifted_letter_keeps_shift_and_uppercases_the_glyph(self, engine):
        engine(lambda keymap: None)
        event = to_event(_key("A", MODKEY_SHIFT))
        assert (event.key, event.char) == ("a", "A")
        assert event.modifiers == frozenset({"shift"})

    def test_space_is_named_but_keeps_its_glyph(self, engine):
        engine(lambda keymap: None)
        event = to_event(_key("Space"))
        assert (event.key, event.char) == ("space", " ")

    def test_named_keys_use_puikits_spelling(self, engine):
        engine(lambda keymap: None)
        assert to_event(_key("Return")).key == "enter"
        assert to_event(_key("Back")).key == "backspace"
        assert to_event(_key("PageDown")).key == "pagedown"

    def test_unshifted_digit_translates(self, engine):
        engine(lambda keymap: None)
        assert to_event(_key("1")).key == "1"

    def test_shifted_digits_come_from_the_layout(self, engine):
        # Which glyph Shift-1 produces is a layout property, so the route
        # asks the OS rather than guessing (see keyroute). Contract §3: the
        # glyph *is* the identity and shift is dropped, since it is already
        # baked in.
        engine(lambda keymap: None)
        event = to_event(_key("1", MODKEY_SHIFT))
        assert (event.key, event.char) == ("!", "!")
        assert event.modifiers == frozenset()

    def test_punctuation_translates(self, engine):
        engine(lambda keymap: None)
        for name, plain, shifted in (("Minus", "-", "_"),
                                     ("Period", ".", ">"),
                                     ("Slash", "/", "?"),
                                     ("Semicolon", ";", ":")):
            assert to_event(_key(name)).char == plain, name
            assert to_event(_key(name, MODKEY_SHIFT)).char == shifted, name

    def test_a_ctrl_chord_is_a_command_not_text(self, engine):
        from keyhac.core.const import MODKEY_CTRL
        engine(lambda keymap: None)
        assert to_event(_key("Slash", MODKEY_CTRL)) is None


class TestModalGrab:
    """The grab itself, at the Keymap level."""

    def test_keys_are_consumed_and_delivered(self, engine):
        fixture = engine(lambda keymap: None)
        seen = []
        fixture.keymap.push_modal_input(seen.append)
        assert fixture.down("A") is True, "the app underneath must see nothing"
        assert fixture.up("A") is True
        assert [str(k) for k in seen] == ["D-A"]

    def test_only_key_downs_reach_the_handler(self, engine):
        fixture = engine(lambda keymap: None)
        seen = []
        fixture.keymap.push_modal_input(seen.append)
        fixture.stroke("A")
        # One down; no key-up echo and no one-shot echo.
        assert len(seen) == 1 and seen[0].down

    def test_modifiers_pass_through_and_are_tracked(self, engine):
        fixture = engine(lambda keymap: None)
        seen = []
        fixture.keymap.push_modal_input(seen.append)
        fixture.down("LShift")
        fixture.down("A")
        # The engine tracks the L/R plane, so the test asks the same
        # question keyroute does - any of the three shift bits.
        assert seen[-1].mod & (MODKEY_SHIFT | MODKEY_SHIFT_L | MODKEY_SHIFT_R), \
            "the handler must see the real modifier state"

    def test_the_tables_come_first_and_the_rest_is_the_grabs(self, engine):
        """Issue #155: a grab no longer outranks the tables - it gets what
        they do not bind."""
        calls, seen = [], []

        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            table["A"] = lambda: calls.append("a")

        fixture = engine(configure)
        fixture.stroke("Shift")             # a first key, so the focus is read
        fixture.keymap.push_modal_input(seen.append)
        fixture.stroke("A")
        fixture.stroke("B")
        assert calls == ["a"]
        assert [str(k) for k in seen] == ["D-B"]

    def test_popping_restores_normal_dispatch(self, engine):
        fixture = engine(lambda keymap: None)
        seen = []
        fixture.keymap.push_modal_input(seen.append)
        fixture.keymap.pop_modal_input()
        assert fixture.stroke("A") == (False, False)
        assert seen == []

    def test_pushing_a_grab_disarms_a_multi_stroke_prefix(self, engine):
        """One modal slot, so a prefix and a grab can never both be up."""
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            sub = keymap.define_keytable(name="sub")
            table["Ctrl-X"] = sub
            sub["A"] = lambda: None

        fixture = engine(configure)
        fixture.down("LCtrl")
        fixture.stroke("X")
        fixture.up("LCtrl")
        assert fixture.keymap._multi_stroke_keytable is not None

        fixture.keymap.push_modal_input(lambda key: None)
        assert fixture.keymap._multi_stroke_keytable is None
        assert fixture.keymap.modal_input_active()

    def test_a_failing_handler_releases_the_grab(self, engine):
        """Pass-through-on-error: a broken candidate window must not leave
        the keyboard captured."""
        fixture = engine(lambda keymap: None)

        def boom(key):
            raise RuntimeError("handler is broken")

        fixture.keymap.push_modal_input(boom)
        fixture.stroke("A")
        assert not fixture.keymap.modal_input_active()


class TestNonActivatingChooser:
    """End to end: a window with no OS focus, filtered from the hook."""

    def _chooser(self, ui_backend, **kwargs):
        _fixture, backend = ui_backend
        items = [("*", "alpha", 1), ("*", "beta", 2), ("*", "alpine", 3)]
        return ChooserWindow(backend, items, activates=False, **kwargs)

    def test_window_is_created_non_activating(self, ui_backend):
        chooser = self._chooser(ui_backend)
        assert chooser.window.window_style.activates is False
        chooser.dismiss()

    def test_typing_filters_without_focus(self, ui_backend):
        fixture, _backend = ui_backend
        chooser = self._chooser(ui_backend)
        assert fixture.keymap.modal_input_active()

        for name in ("A", "L", "P"):
            fixture.stroke(name)

        assert chooser._edit.text == "alp"
        assert [c.display for c in chooser._filtered] == ["alpha", "alpine"]
        chooser.dismiss()

    def test_backspace_and_arrows_reach_the_widgets(self, ui_backend):
        fixture, _backend = ui_backend
        chooser = self._chooser(ui_backend)
        for name in ("A", "L", "X"):
            fixture.stroke(name)
        assert chooser._filtered == []

        fixture.stroke("Back")
        assert chooser._edit.text == "al"
        assert len(chooser._filtered) == 2

        fixture.stroke("Down")      # steps out of the field onto the first row
        assert chooser._list.selected == 0
        fixture.stroke("Down")
        assert chooser._list.selected == 1
        chooser.dismiss()

    def test_enter_chooses_and_releases_the_grab(self, ui_backend):
        fixture, _backend = ui_backend
        chosen = []
        chooser = self._chooser(ui_backend,
                                on_selected=lambda item, mod: chosen.append(item))
        fixture.stroke("B")
        fixture.stroke("Return")
        assert [c.payload for c in chosen] == [("*", "beta", 2)]
        assert not fixture.keymap.modal_input_active()

    def test_escape_cancels_and_releases_the_grab(self, ui_backend):
        fixture, _backend = ui_backend
        canceled = []
        chooser = self._chooser(ui_backend,
                                on_canceled=lambda: canceled.append(True))
        fixture.stroke("Escape")
        assert canceled == [True]
        assert not fixture.keymap.modal_input_active()

    def test_shift_enter_carries_its_modifier(self, ui_backend):
        fixture, _backend = ui_backend
        flags = []
        chooser = self._chooser(ui_backend,
                                on_selected=lambda item, mod: flags.append(mod))
        fixture.down("LShift")
        fixture.stroke("Return")
        fixture.up("LShift")
        assert flags == [MODKEY_SHIFT]

    def test_not_taking_focus_is_the_default(self, ui_backend):
        fixture, backend = ui_backend
        chooser = ChooserWindow(backend, [("*", "alpha", 1)])
        style = chooser.window.window_style
        assert style.activates is False
        # Clickable without taking the keyboard (puikit PR #126). It travels
        # with `activates` rather than being separately settable, so no
        # caller can ask for a combination that does not work.
        assert style.overlay_input == "mouse"
        # The panel's mask forces a title bar; frameless hides it again.
        assert style.frameless is True
        assert fixture.keymap.modal_input_active()
        chooser.dismiss()

    def test_an_activating_chooser_takes_no_grab(self, ui_backend):
        fixture, backend = ui_backend
        chooser = ChooserWindow(backend, [("*", "alpha", 1)], activates=True)
        assert chooser.window.window_style.overlay_input == "none"
        assert not fixture.keymap.modal_input_active()
        chooser.dismiss()

    def test_escape_closes_the_window_even_while_an_action_runs(self, ui_backend):
        """Esc is offered to a running ThreadedAction before the key tables,
        but not while a candidate window holds the keyboard."""
        import threading
        from keyhac.core.action import ThreadedAction

        fixture, _backend = ui_backend
        started, release = threading.Event(), threading.Event()

        class _Slow(ThreadedAction):
            def run(self):
                started.set()
                release.wait(5)

            def finished(self, result):
                pass

        _Slow()()
        assert started.wait(5)

        canceled = []
        chooser = self._chooser(ui_backend,
                                on_canceled=lambda: canceled.append(True))
        fixture.stroke("Escape")
        release.set()
        assert canceled == [True]
        assert not fixture.keymap.modal_input_active()


class TestTheKeymapInsideTheChooser:
    """Issue #155: while a chooser holds the keyboard, the user's key tables
    are matched as if it were focused, and their output is the window's."""

    @pytest.fixture
    def opened(self, engine):
        backends = []

        def make(configure, platform="mac", items=None):
            fixture = engine(configure, platform=platform)
            from puikit.backends.memory_backend import MemoryBackend
            backend = MemoryBackend(width=100, height=30)
            backend.open()
            backends.append(backend)
            runtime.backend = backend
            fixture.stroke("Shift")         # read the focus, as any key would
            fixture.hook.sent.clear()
            items = items or [("*", "alpha", 1), ("*", "beta", 2),
                              ("*", "alpine", 3)]
            return fixture, ChooserWindow(backend, items, activates=False)

        yield make
        runtime.backend = None
        for backend in backends:
            backend.close()

    @staticmethod
    def _chord(fixture, mod, name):
        fixture.down(mod)
        fixture.stroke(name)
        fixture.up(mod)

    @staticmethod
    def _keys_to_os(fixture):
        """What reached the OS, minus the modifier bookkeeping around it."""
        mods = {"Ctrl", "LCtrl", "Shift", "LShift", "Alt", "LAlt", "Cmd",
                "LCmd", "Fn", "LFn"}
        return [n for n in fixture.sent_names() if n[2:] not in mods]

    def test_a_remap_moves_the_selection(self, opened):
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            table["Ctrl-J"] = "Down"
            table["Ctrl-K"] = "Up"

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "J")
        self._chord(fixture, "LCtrl", "J")
        assert chooser.in_list and chooser._list.selected == 1
        self._chord(fixture, "LCtrl", "K")
        assert chooser._list.selected == 0
        assert self._keys_to_os(fixture) == [], "nothing for the app underneath"
        chooser.dismiss()

    def test_modifier_keys_still_reach_the_os(self, opened):
        fixture, chooser = opened(
            lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        assert fixture.down("LCtrl") is False
        assert fixture.up("LCtrl") is False
        chooser.dismiss()

    def test_unbound_keys_keep_their_chooser_meaning(self, opened):
        fixture, chooser = opened(
            lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        for name in ("A", "L"):
            assert fixture.stroke(name) == (True, True)
        fixture.stroke("Down")
        assert chooser._edit.text == "al"
        assert chooser._list.selected == 0
        chooser.dismiss()

    def test_a_functions_output_reaches_the_filter(self, opened):
        def configure(keymap):
            def type_al():
                with keymap.get_input_context() as ctx:
                    ctx.send_key("A")
                    ctx.send_text("lp")
            keymap.define_keytable(focus_path_pattern="*")["Ctrl-T"] = type_al

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "T")
        assert chooser._edit.text == "alp"
        assert [c.display for c in chooser._filtered] == ["alpha", "alpine"]
        assert self._keys_to_os(fixture) == []
        chooser.dismiss()

    def test_text_output_is_not_limited_to_ascii(self, opened):
        def configure(keymap):
            def type_japanese():
                with keymap.get_input_context() as ctx:
                    ctx.send_text("議事録")
            keymap.define_keytable(focus_path_pattern="*")["Ctrl-T"] = type_japanese

        fixture, chooser = opened(configure,
                                  items=[("*", "議事録メモ", 1), ("*", "alpha", 2)])
        self._chord(fixture, "LCtrl", "T")
        assert chooser._edit.text == "議事録"
        assert [c.display for c in chooser._filtered] == ["議事録メモ"]
        chooser.dismiss()

    def test_a_multi_stroke_prefix_works(self, opened):
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            sub = keymap.define_keytable(name="Ctrl-X")
            table["Ctrl-X"] = sub
            sub["N"] = "Down"

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "X")
        fixture.stroke("N")
        assert chooser._edit.text == ""
        assert chooser.in_list and chooser._list.selected == 0
        chooser.dismiss()

    def test_an_unmatched_key_after_a_prefix_is_consumed(self, opened):
        """The inherited unmatched-consume rule holds here too."""
        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            sub = keymap.define_keytable(name="Ctrl-X")
            table["Ctrl-X"] = sub
            sub["N"] = "Down"

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "X")
        fixture.stroke("Q")
        fixture.stroke("A")
        assert chooser._edit.text == "a"
        chooser.dismiss()

    def test_a_user_modifier_works(self, opened):
        def configure(keymap):
            keymap.define_modifier("RAlt", "RUser0")
            keymap.define_keytable(focus_path_pattern="*")["User0-J"] = "Down"

        fixture, chooser = opened(configure)
        self._chord(fixture, "RAlt", "J")
        assert chooser.in_list and chooser._list.selected == 0
        assert chooser._edit.text == ""
        chooser.dismiss()

    def test_a_chooser_table_applies_only_while_it_is_open(self, opened):
        calls = []

        def configure(keymap):
            table = keymap.define_keytable(app="Keyhac", title="Chooser")
            table["Ctrl-J"] = "Down"
            table["Ctrl-Q"] = lambda: calls.append("q")

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "J")
        assert chooser.in_list and chooser._list.selected == 0
        chooser.dismiss()

        fixture.hook.sent.clear()
        self._chord(fixture, "LCtrl", "Q")
        assert calls == [], "back on the app, the chooser's table is gone"

    def test_the_apps_own_table_does_not_apply_inside(self, opened):
        calls = []

        def configure(keymap):
            keymap.define_keytable(app="testapp")["Ctrl-J"] = \
                lambda: calls.append("app")

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "J")
        assert calls == []
        chooser.dismiss()
        self._chord(fixture, "LCtrl", "J")
        assert calls == ["app"], "and it is back once the chooser closes"

    def test_keymap_focus_stays_the_real_one(self, opened):
        fixture, chooser = opened(
            lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        fixture.stroke("A")
        assert fixture.keymap.focus.app_name == "testapp"
        assert "focus path: /Keyhac/Chooser" in fixture.keymap.describe_keymap()
        chooser.dismiss()
        assert "focus path: /testapp/" in fixture.keymap.describe_keymap()

    def test_a_condition_function_sees_the_chooser(self, opened):
        seen = []

        def configure(keymap):
            def condition(focus):
                seen.append((focus.app_name, focus.window_title))
                return True
            keymap.define_keytable(custom_condition_func=condition)

        fixture, chooser = opened(configure)
        assert ("Keyhac", "Chooser") in seen
        chooser.dismiss()

    def test_output_with_no_chooser_event_is_dropped(self, opened):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")["Ctrl-J"] = "Cmd-S"

        fixture, chooser = opened(configure)
        self._chord(fixture, "LCtrl", "J")
        assert chooser._edit.text == ""
        assert self._keys_to_os(fixture) == [], "not to the app underneath"
        chooser.dismiss()

    def test_an_error_in_a_binding_does_not_leak_the_key(self, opened):
        def configure(keymap):
            def boom():
                raise RuntimeError("broken binding")
            keymap.define_keytable(focus_path_pattern="*")["Ctrl-J"] = boom

        fixture, chooser = opened(configure)
        fixture.down("LCtrl")
        assert fixture.down("J") is True
        assert fixture.up("J") is True
        fixture.up("LCtrl")
        chooser.dismiss()

    def test_a_lone_alt_is_still_fenced_on_windows(self, opened):
        """The OS saw Alt go down and the key consumed: without the Ctrl tap,
        releasing Alt opens the menu bar of the application underneath."""
        fixture, chooser = opened(
            lambda keymap: keymap.define_keytable(focus_path_pattern="*"),
            platform="windows")
        fixture.down("LAlt")
        fixture.stroke("A")
        fixture.up("LAlt")
        assert "D-LCtrl" in fixture.sent_names()
        chooser.dismiss()

    def test_a_remap_on_windows_fences_alt_too(self, opened):
        fixture, chooser = opened(
            lambda keymap: keymap.define_keytable(
                focus_path_pattern="*").__setitem__("Alt-J", "Down"),
            platform="windows")
        fixture.down("LAlt")
        fixture.stroke("J")
        fixture.up("LAlt")
        assert chooser.in_list and chooser._list.selected == 0
        assert "D-LCtrl" in fixture.sent_names()
        assert "D-Down" not in fixture.sent_names()
        chooser.dismiss()
