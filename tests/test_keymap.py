"""Keymap engine behavior, driven through FakeInputHook."""

from keyhac.platform.base import Focus


class TestKeyToKey:

    def test_remap_consumes_and_injects(self, engine):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Fn-J"] = "Left"

        e = engine(configure)
        assert e.down("Fn") is False          # modifier key passes through
        assert e.down("J") is True            # remapped -> consumed
        assert e.sent_names() == ["U-Fn", "D-Left", "U-Left", "D-Fn"]
        assert e.up("J") is False             # unbound key-up passes through
        assert e.up("Fn") is False

    def test_sequence_output(self, engine):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Ctrl-1"] = "A", "B"

        e = engine(configure)
        e.down("LCtrl")
        e.hook.clear()
        assert e.down("1") is True
        assert e.sent_names() == ["U-LCtrl", "D-A", "U-A", "D-B", "U-B", "D-LCtrl"]

    def test_callable_action(self, engine):
        calls = []

        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Ctrl-F1"] = lambda: calls.append(1)

        e = engine(configure)
        e.down("LCtrl")
        assert e.down("F1") is True
        assert calls == [1]
        assert e.hook.sent == []              # callable injected nothing

    def test_unbound_key_passes_through(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        assert e.down("A") is False
        assert e.up("A") is False
        assert e.hook.sent == []

    def test_short_alias_expression(self, engine):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["C-S-X"] = "Escape"

        e = engine(configure)
        e.down("LCtrl")
        e.down("LShift")
        assert e.down("X") is True


class TestReplaceKey:

    def test_replace(self, engine):
        def configure(keymap):
            keymap.replace_key("A", "B")
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        assert e.down("A") is True
        assert e.up("A") is True
        assert e.sent_names() == ["D-B", "U-B"]

    def test_replaced_key_matches_keytable_under_new_meaning(self, engine):
        def configure(keymap):
            keymap.replace_key("CapsLock", "F13")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["F13"] = "Escape"

        e = engine(configure)
        assert e.down("CapsLock") is True
        assert e.sent_names() == ["D-Escape", "U-Escape"]


class TestMultiStroke:

    def _configure(self, keymap):
        kt = keymap.define_keytable(focus_path_pattern="*")
        ms = keymap.define_keytable(name="Ctrl-X")
        kt["Ctrl-X"] = ms
        ms["Ctrl-O"] = "F1"

    def test_second_stroke_fires(self, engine):
        e = engine(self._configure)
        e.down("LCtrl")
        assert e.down("X") is True            # enter multi-stroke
        e.up("X")
        e.hook.clear()
        assert e.down("O") is True            # Ctrl-O inside multi-stroke
        assert "D-F1" in e.sent_names()

    def test_unmatched_key_leaves_multi_stroke_and_is_consumed(self, engine):
        e = engine(self._configure)
        e.down("LCtrl")
        e.down("X")
        e.up("X")
        assert e.down("Z") is True            # swallowed while leaving
        e.up("Z")
        e.hook.clear()
        assert e.down("O") is False           # multi-stroke is over

    def test_modifier_key_does_not_leave_multi_stroke(self, engine):
        e = engine(self._configure)
        e.down("LCtrl")
        e.down("X")
        e.up("X")
        e.up("LCtrl")                          # releasing/pressing modifiers is fine
        e.down("LCtrl")
        assert e.down("O") is True
        assert "D-F1" in e.sent_names()


class TestOneShot:

    def _configure(self, keymap):
        kt = keymap.define_keytable(focus_path_pattern="*")
        kt["O-RCmd"] = "Escape"

    def test_oneshot_fires_on_lone_press(self, engine):
        e = engine(self._configure)
        e.down("RCmd")
        e.up("RCmd")
        assert e.sent_names() == ["D-Escape", "U-Escape"]

    def test_oneshot_canceled_by_intervening_key(self, engine):
        e = engine(self._configure)
        e.down("RCmd")
        e.down("A")
        e.up("A")
        e.up("RCmd")
        assert "D-Escape" not in e.sent_names()


class TestUserModifier:

    def _configure(self, keymap):
        keymap.define_modifier("RAlt", "User0")
        kt = keymap.define_keytable(focus_path_pattern="*")
        kt["U0-J"] = "Down"

    def test_user_modifier_key_is_swallowed(self, engine):
        e = engine(self._configure)
        assert e.down("RAlt") is True
        assert e.up("RAlt") is True
        assert e.hook.sent == []               # never emitted physically

    def test_user_modified_key(self, engine):
        e = engine(self._configure)
        e.down("RAlt")
        assert e.down("J") is True
        # user modifier is not reconciled into physical output
        assert e.sent_names() == ["D-Down", "U-Down"]

    def test_user2_user3_supported(self, engine):
        def configure(keymap):
            keymap.define_modifier("RCtrl", "User3")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["U3-K"] = "Up"

        e = engine(configure)
        e.down("RCtrl")
        assert e.down("K") is True
        assert e.sent_names() == ["D-Up", "U-Up"]


class TestUpDownBindings:

    def test_down_and_up_conditions(self, engine):
        marks = []

        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["D-F2"] = lambda: marks.append("down")
            kt["U-F2"] = lambda: marks.append("up")

        e = engine(configure)
        assert e.down("F2") is True
        assert e.up("F2") is True
        assert marks == ["down", "up"]


class TestFocusSwitching:

    def _configure(self, keymap):
        kt_global = keymap.define_keytable(focus_path_pattern="*")
        kt_global["F1"] = "A"
        kt_edit = keymap.define_keytable(app="editor*")
        kt_edit["F1"] = "B"

    def test_specific_table_overrides_global(self, engine):
        e = engine(self._configure)
        e.focus_provider.focus = Focus(app_name="editorX", pid=1,
                                       window_title="w", path="/editorX/w")
        e.down("F1")
        assert e.sent_names() == ["D-B", "U-B"]

    def test_global_only_elsewhere(self, engine):
        e = engine(self._configure)
        e.focus_provider.focus = Focus(app_name="terminal", pid=2,
                                       window_title="t", path="/terminal/t")
        e.down("F1")
        assert e.sent_names() == ["D-A", "U-A"]

    def test_focus_change_rebuilds(self, engine):
        e = engine(self._configure)
        e.focus_provider.focus = Focus(app_name="editorX", pid=1,
                                       window_title="w", path="/editorX/w")
        e.stroke("F1")
        e.hook.clear()
        e.focus_provider.focus = Focus(app_name="terminal", pid=2,
                                       window_title="t", path="/terminal/t")
        e.stroke("F1")
        assert e.sent_names()[:2] == ["D-A", "U-A"]


class TestWindowsPlatform:

    def test_class_name_condition(self, engine):
        def configure(keymap):
            kt = keymap.define_keytable(app="notepad.exe", class_name="Edit")
            kt["C-A"] = "Home"

        e = engine(configure, platform="windows")
        e.focus_provider.focus = Focus(app_name="notepad", pid=1,
                                       window_title="Untitled - Notepad",
                                       class_name="Edit",
                                       path="/notepad/Edit(Untitled - Notepad)")
        e.down("LCtrl")
        assert e.down("A") is True
        assert "D-Home" in e.sent_names()

    def test_win_modifier(self, engine):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Win-E"] = "F5"

        e = engine(configure, platform="windows")
        e.down("LWin")
        assert e.down("E") is True
        assert "D-F5" in e.sent_names()


class TestHookRestore:

    def test_modifier_reset_on_restore(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        e.down("LCtrl")
        assert e.keymap._modifier != 0
        e.hook.restore()
        assert e.keymap._modifier == 0


class TestConfigErrorContainment:

    def test_action_exception_passes_key_through(self, engine):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            def boom():
                raise RuntimeError("user config bug")
            kt["F1"] = boom

        e = engine(configure)
        # The exception is logged; the event is passed through, not raised
        assert e.down("F1") is False


class TestMacroRecording:

    def test_record_normalize_playback(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        rb = e.keymap.replay_buffer
        rb.start_recording()
        e.stroke("A")
        e.down("B")            # unmatched down - dropped by normalization
        rb.stop_recording()
        assert rb.seq == [(e.vk("A"), True), (e.vk("A"), False)]

        e.hook.clear()
        rb.playback()
        # replay events re-enter the engine (kind="replay" via FakeInputHook)
        assert (e.vk("A"), True, True) in e.hook.sent

    def test_playback_of_bound_key_reevaluated(self, engine):
        fired = []

        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["F5"] = lambda: fired.append(1)

        e = engine(configure)
        rb = e.keymap.replay_buffer
        rb.start_recording()
        e.stroke("F5")
        rb.stop_recording()
        fired.clear()
        rb.playback()
        assert fired == [1]    # the replayed key ran the binding again


class TestInputText:

    def test_send_text_through_hook(self, engine):
        def configure(keymap):
            keymap.define_keytable(focus_path_pattern="*")

        e = engine(configure)
        from keyhac.core.action import InputText
        InputText("hello, 世界")()
        assert e.hook.sent_text == ["hello, 世界"]
