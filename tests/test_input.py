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


class TestSendText:

    def _record_interleaved(self, hook):
        """Route send()/send_text() into one list to assert their ordering."""
        events = []
        hook.send = lambda seq, replay=False: events.append(("keys", list(seq)))
        hook.send_text = lambda s: events.append(("text", s))
        return events

    def test_send_text_plain(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        with e.keymap.get_input_context() as ctx:
            ctx.send_text("hello")
        assert e.hook.sent_text == ["hello"]
        assert e.hook.sent == []  # no modifiers held: no reconciliation events

    def test_send_text_releases_held_modifiers(self, engine):
        # Issue #2: with the triggering modifier (Fn) still physically held,
        # the injected text events became system shortcuts (Globe-A = Dock).
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        e.down("Fn")
        e.hook.clear()
        events = self._record_interleaved(e.hook)
        with e.keymap.get_input_context() as ctx:
            ctx.send_text("me@example.com")
        fn = e.vk("Fn")
        assert events == [
            ("keys", [(fn, False)]),        # Fn released before the text
            ("text", "me@example.com"),
            ("keys", [(fn, True)]),         # and restored after
        ]

    def test_send_text_between_keys_keeps_order(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        events = self._record_interleaved(e.hook)
        with e.keymap.get_input_context() as ctx:
            ctx.send_key("Tab")
            ctx.send_text("x")
            ctx.send_key("Enter")
        tab, enter = e.vk("Tab"), e.vk("Enter")
        assert events == [
            ("keys", [(tab, True), (tab, False)]),
            ("text", "x"),
            ("keys", [(enter, True), (enter, False)]),
        ]

    def test_send_text_outside_context_raises(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        ctx = e.keymap.get_input_context()
        with pytest.raises(ValueError):
            ctx.send_text("x")
