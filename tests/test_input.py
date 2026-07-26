"""InputContext batching and modifier reconciliation."""

import pytest


class TestInputContext:

    def test_explicit_down_up(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        with e.keymap.get_input_context() as ctx:
            ctx.send_key("D-LShift")
            ctx.send_key("Tab")
            ctx.send_key("U-LShift")
        assert e.sent_names() == ["D-LShift", "D-Tab", "U-Tab", "U-LShift"]

    def test_modifier_reconciliation_around_stroke(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        e.down("LCtrl")           # engine now tracks Ctrl as held
        e.hook.clear()
        with e.keymap.get_input_context() as ctx:
            ctx.send_key("Shift-Tab")
        # Shift pressed, Ctrl released for the stroke, both restored after
        assert e.sent_names() == ["D-LShift", "U-LCtrl", "D-Tab", "U-Tab",
                                  "D-LCtrl", "U-LShift"]

    def test_output_uses_left_side_modifiers(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        with e.keymap.get_input_context() as ctx:
            ctx.send_key("Ctrl-C")           # generic Ctrl in output
        assert e.sent_names() == ["D-LCtrl", "D-C", "U-C", "U-LCtrl"]

    def test_outside_context_raises(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        ctx = e.keymap.get_input_context()
        with pytest.raises(ValueError):
            ctx.send_key("A")

    def test_replay_flag_reenters_engine(self, engine):
        seen = []

        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["F1"] = lambda: seen.append("F1")

        e = engine(configure)
        with e.keymap.get_input_context(replay=True) as ctx:
            ctx.send_key("F1")
        # replay events go back through the keymap (FakeInputHook mimics this)
        assert seen == ["F1"]
