"""Mouse output (InputContext + actions) and one-shot cancel on mouse.

Engine-level with the fake hook; the Windows injection itself is covered
live in tests/test_win_mouse.py.
"""

import pytest


def _mouse_events(hook):
    return [event for event, _replay in hook.sent_mouse]


class TestInputContextMouse:

    def test_move_keeps_modifiers(self, engine):
        e = engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        e.down("LCtrl")
        e.hook.clear()
        with e.keymap.get_input_context() as ctx:
            ctx.send_mouse_move(15, -9)
        assert _mouse_events(e.hook) == [("move", 15, -9)]
        assert e.sent_names() == []  # no modifier fiddling around a move

    def test_click_releases_and_restores_modifiers(self, engine):
        e = engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        e.down("LCtrl")
        e.hook.clear()
        with e.keymap.get_input_context() as ctx:
            ctx.send_mouse_button("left")
        # Ctrl up before the click, down again after (keyhac-win behavior)
        assert e.sent_names() == ["U-LCtrl", "D-LCtrl"]
        assert _mouse_events(e.hook) == [("left", True), ("left", False)]

    def test_button_down_up_split(self, engine):
        e = engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        with e.keymap.get_input_context() as ctx:
            ctx.send_mouse_button("right", down=True)
        with e.keymap.get_input_context() as ctx:
            ctx.send_mouse_button("right", down=False)
        assert _mouse_events(e.hook) == [("right", True), ("right", False)]

    def test_wheels(self, engine):
        e = engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        with e.keymap.get_input_context() as ctx:
            ctx.send_mouse_wheel(1.0)
            ctx.send_mouse_horizontal_wheel(-2.0)
        assert _mouse_events(e.hook) == [("wheel", 1.0), ("hwheel", -2.0)]

    def test_invalid_button_raises(self, engine):
        e = engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        with e.keymap.get_input_context() as ctx:
            with pytest.raises(ValueError):
                ctx.send_mouse_button("side")

    def test_ordering_across_channels(self, engine):
        """Key -> mouse -> key must flush as three ordered platform calls."""
        e = engine(lambda keymap: keymap.define_keytable(focus_path_pattern="*"))
        calls = []
        e.hook.send = lambda seq, replay=False: calls.append(("keys", list(seq)))
        e.hook.send_mouse = lambda seq, replay=False: calls.append(("mouse", list(seq)))
        with e.keymap.get_input_context() as ctx:
            ctx.send_key("A")
            ctx.send_mouse_move(1, 1)
            ctx.send_key("B")
        kinds = [kind for kind, _payload in calls]
        assert kinds == ["keys", "mouse", "keys"]


class TestMouseActions:

    def test_action_classes_send_through_the_context(self, engine):
        from keyhac.actions import (
            MouseButtonClick, MouseHorizontalWheel, MouseMove, MouseWheel,
        )

        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["F1"] = MouseMove(30, 40)
            kt["F2"] = MouseButtonClick("middle")
            kt["F3"] = MouseWheel(-1.0)
            kt["F4"] = MouseHorizontalWheel(0.5)

        e = engine(configure)
        for key in ("F1", "F2", "F3", "F4"):
            # The bound key-down is consumed; the up passes through (the
            # engine's usual semantic for bound plain keys).
            down_consumed, _up = e.stroke(key)
            assert down_consumed is True
        assert _mouse_events(e.hook) == [
            ("move", 30, 40), ("middle", True), ("middle", False),
            ("wheel", -1.0), ("hwheel", 0.5),
        ]

    def test_invalid_button_fails_at_config_load(self):
        from keyhac.actions import MouseButtonClick
        with pytest.raises(ValueError):
            MouseButtonClick("side")


class TestOneShotMouseCancel:

    def _engine(self, engine):
        fired = []

        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["O-RCmd"] = lambda: fired.append("oneshot")

        return engine(configure), fired

    def test_one_shot_fires_without_mouse(self, engine):
        e, fired = self._engine(engine)
        e.stroke("RCmd")
        assert fired == ["oneshot"]

    def test_mouse_input_cancels_pending_one_shot(self, engine):
        e, fired = self._engine(engine)
        e.down("RCmd")
        e.hook.mouse()          # physical click/wheel while the key is held
        e.up("RCmd")
        assert fired == []
