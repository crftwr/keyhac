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

    def test_shifted_punctuation_has_no_portable_spelling(self, engine):
        # Which glyph Shift-1 produces is a layout property; a vk does not
        # say.  The route drops it rather than guessing (see keyroute).
        engine(lambda keymap: None)
        assert to_event(_key("1", MODKEY_SHIFT)) is None


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

    def test_popping_restores_normal_dispatch(self, engine):
        calls = []

        def configure(keymap):
            table = keymap.define_keytable(focus_path_pattern="*")
            table["A"] = lambda: calls.append("a")

        fixture = engine(configure)
        fixture.keymap.push_modal_input(lambda key: None)
        fixture.stroke("A")
        assert calls == [], "the grab outranks the key table"

        fixture.keymap.pop_modal_input()
        fixture.stroke("A")
        assert calls == ["a"]

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
        assert chosen == [("*", "beta", 2)]
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
        # Clickable without taking the keyboard: on macOS that is one
        # specific window kind, so the flags travel with `activates` rather
        # than being independently settable into a combination that does not
        # work (puikit PR #126).
        assert style.nonactivating_panel is True
        assert style.becomes_key_on_demand is True
        # The panel's mask forces a title bar; frameless hides it again.
        assert style.frameless is True
        assert fixture.keymap.modal_input_active()
        chooser.dismiss()

    def test_an_activating_chooser_takes_no_grab(self, ui_backend):
        fixture, backend = ui_backend
        chooser = ChooserWindow(backend, [("*", "alpha", 1)], activates=True)
        assert chooser.window.window_style.nonactivating_panel is False
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
