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


class TestLoneWinAltCancel:
    """A held Win/Alt whose companion key Keyhac consumed looks like a lone
    tap to Windows - Start menu, menu bar - so a Ctrl tap marks it used.
    Port of keyhac-win's cancel_oneshot_win_alt / _cancelOneshotWinAlt."""

    def _configure(self, keymap):
        kt = keymap.define_keytable(focus_path_pattern="*")
        kt["Alt-J"] = lambda: None
        kt["Win-J"] = lambda: None
        kt["Alt-Ctrl-J"] = lambda: None
        kt["Alt-M"] = keymap.define_keytable(name="sub")

    def test_callable_under_lone_alt(self, engine):
        e = engine(self._configure, platform="windows")
        e.down("LAlt")
        e.hook.clear()
        assert e.down("J") is True
        assert e.sent_names() == ["D-LCtrl", "U-LCtrl"]

    def test_callable_under_lone_win(self, engine):
        e = engine(self._configure, platform="windows")
        e.down("LWin")
        e.hook.clear()
        assert e.down("J") is True
        assert e.sent_names() == ["D-LCtrl", "U-LCtrl"]

    def test_entering_multi_stroke(self, engine):
        e = engine(self._configure, platform="windows")
        e.down("LAlt")
        e.hook.clear()
        assert e.down("M") is True
        assert e.sent_names() == ["D-LCtrl", "U-LCtrl"]

    def test_not_a_lone_modifier(self, engine):
        """Alt+Ctrl released together opens nothing, so nothing to cancel."""
        e = engine(self._configure, platform="windows")
        e.down("LAlt")
        e.down("LCtrl")
        e.hook.clear()
        assert e.down("J") is True
        assert e.hook.sent == []

    def test_callable_under_alt_and_a_user_modifier(self, engine):
        """A user modifier is never emitted, so Windows still saw a lone Alt -
        the cancel has to look past the user bits to see it."""
        def configure(keymap):
            keymap.define_modifier("RCtrl", "User0")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["U0-Alt-J"] = lambda: None

        e = engine(configure, platform="windows")
        e.down("RCtrl")
        e.down("LAlt")
        e.hook.clear()
        assert e.down("J") is True
        assert e.sent_names() == ["D-LCtrl", "U-LCtrl"]

    def test_output_under_alt_and_a_user_modifier(self, engine):
        """Same blind spot on the output path: releasing Alt around the batch
        is the lone tap, and the user modifier does not fence it."""
        def configure(keymap):
            keymap.define_modifier("RCtrl", "User0")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["U0-Alt-J"] = "Down"

        e = engine(configure, platform="windows")
        e.down("RCtrl")
        e.down("LAlt")
        e.hook.clear()
        assert e.down("J") is True
        assert e.sent_names() == ["D-LCtrl", "U-LCtrl", "U-LAlt",
                                  "D-Down", "U-Down",
                                  "D-LAlt", "D-LCtrl", "U-LCtrl"]

    def test_macos_has_nothing_to_cancel(self, engine):
        e = engine(self._configure)
        e.down("LAlt")
        e.hook.clear()
        assert e.down("J") is True
        assert e.hook.sent == []


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

    def test_windows_key_is_refused(self, engine):
        """Keyhac can keep the Win key out of every application, but not out
        of the shell - a Win-based user modifier still opens the Game Bar on
        Win+G, and the Game Bar swallows that keystroke. Refused, and the key
        stays the Win modifier it was."""
        def configure(keymap):
            keymap.define_modifier("LWin", "LUser0")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["U0-J"] = "Down"
            kt["Win-K"] = "Up"

        e = engine(configure, platform="windows")
        e.down("LWin")
        assert e.down("J") is False            # U0-J never armed
        assert e.down("K") is True             # LWin is still Win
        # ... and being a real modifier, it is released around the output,
        # each transition fenced by the lone-Win/Alt cancelling Ctrl tap
        assert e.sent_names() == ["D-LCtrl", "U-LCtrl", "U-LWin",
                                  "D-Up", "U-Up",
                                  "D-LWin", "D-LCtrl", "U-LCtrl"]

    def test_windows_key_retired_through_replace_key(self, engine, caplog):
        """The sample configuration's route to User0 on Windows: rename the
        Win keys to codes Windows has no meaning for, and make one of them
        the modifier. Not blocked by the define_modifier refusal, and not
        reported as a redefinition - 235 was not a modifier."""
        def configure(keymap):
            keymap.replace_key("LWin", 235)
            keymap.replace_key("RWin", 255)
            keymap.define_modifier(235, "User0")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["U0-J"] = "Down"

        with caplog.at_level("INFO", logger="keyhac.Keymap"):
            e = engine(configure, platform="windows")
        assert not any("modifier and is now" in r.message
                       for r in caplog.records)
        assert e.down("LWin") is True          # consumed, never emitted
        assert e.hook.sent == []
        assert e.down("J") is True
        assert e.sent_names() == ["D-Down", "U-Down"]

    def test_redefining_a_modifier_is_reported(self, engine, caplog):
        """Legitimate, so not a warning - but the key stops being the modifier
        it was, everywhere, and that is worth saying out loud."""
        def configure(keymap):
            keymap.define_modifier("RAlt", "RUser0")
            keymap.define_keytable(focus_path_pattern="*")

        with caplog.at_level("INFO", logger="keyhac.Keymap"):
            engine(configure)
        assert any("RAlt was the RAlt modifier and is now RUser0" in r.message
                   for r in caplog.records)

    def test_defining_a_plain_key_says_nothing(self, engine, caplog):
        def configure(keymap):
            keymap.define_modifier("F20", "User0")
            keymap.define_keytable(focus_path_pattern="*")

        with caplog.at_level("INFO", logger="keyhac.Keymap"):
            engine(configure)
        assert not any("modifier and is now" in r.message
                       for r in caplog.records)

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


class TestReloadWhileUnhooked:
    """The console's hook checkbox reconfigures on re-enable, while the hook
    is still uninstalled; the modifier release must not run then (issue #25)."""

    def test_reload_sends_nothing_while_uninstalled(self, engine):
        e = engine(lambda keymap: None)
        e.hook.uninstall()
        e.hook.sent.clear()
        e.keymap.configure()
        assert e.hook.sent == []

    def test_reload_still_releases_modifiers_while_installed(self, engine):
        e = engine(lambda keymap: None)
        e.hook.sent.clear()
        e.keymap.configure()
        assert e.hook.sent
        assert all(down is False for _vk, down, _replay in e.hook.sent)


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


class TestUnreachableModifierWarning:
    """Cmd-/Fn- assignments parse on Windows but no key ever sets those bits."""

    def _warnings(self, e, caplog):
        import logging
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="keyhac.Keymap"):
            e.keymap._warn_unreachable_modifiers()
        return [r.getMessage() for r in caplog.records]

    def test_mac_modifiers_reported_on_windows(self, engine, caplog):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Fn-V"] = "Left"
            kt["Cmd-Shift-V"] = "Left"
            kt["Ctrl-A"] = "Home"

        e = engine(configure, platform="windows")
        messages = self._warnings(e, caplog)
        assert len(messages) == 1
        assert "Cmd, Fn" in messages[0] and "Ctrl" not in messages[0]

    def test_no_warning_when_every_modifier_is_reachable(self, engine, caplog):
        def configure(keymap):
            keymap.define_modifier("RAlt", "RUser0")
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["User0-J"] = "Left"
            kt["Ctrl-Shift-A"] = "Home"

        e = engine(configure, platform="windows")
        assert self._warnings(e, caplog) == []

    def test_multi_stroke_tables_are_scanned(self, engine, caplog):
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt_sub = keymap.define_keytable(name="Ctrl-X")
            kt["Ctrl-X"] = kt_sub
            kt_sub["Cmd-O"] = "Ctrl-O"

        e = engine(configure, platform="windows")
        messages = self._warnings(e, caplog)
        assert len(messages) == 1 and "Cmd" in messages[0]


class FakeAppControl:

    def __init__(self):
        self.edited = []

    def activate_pid(self, pid):
        return True

    def launch(self, app_name):
        pass

    def edit_file(self, path, editor=None):
        self.edited.append((path, editor))


class TestEditConfig:

    def test_default_editor_routes_to_app_control(self, engine):
        e = engine(lambda keymap: None)
        e.keymap.app_control = FakeAppControl()
        e.keymap.edit_config()
        # Empty editor setting -> None, the platform-default marker.
        assert e.keymap.app_control.edited == [(e.keymap._config_path, None)]

    def test_string_editor_is_passed_through(self, engine):
        e = engine(lambda keymap: None)
        e.keymap.app_control = FakeAppControl()
        e.keymap.editor = "CotEditor"
        e.keymap.edit_config()
        assert e.keymap.app_control.edited == [(e.keymap._config_path, "CotEditor")]

    def test_callable_editor_receives_the_path(self, engine):
        e = engine(lambda keymap: None)
        e.keymap.app_control = FakeAppControl()
        calls = []
        e.keymap.editor = calls.append
        e.keymap.edit_config()
        assert calls == [e.keymap._config_path]
        assert e.keymap.app_control.edited == []

    def _messages(self, caplog, level, call):
        import logging
        caplog.clear()
        with caplog.at_level(level, logger="keyhac.Keymap"):
            call()
        return [r.getMessage() for r in caplog.records]

    def test_callable_editor_error_is_contained(self, engine, caplog):
        import logging
        e = engine(lambda keymap: None)

        def bad_editor(path):
            raise RuntimeError("boom")

        e.keymap.editor = bad_editor
        messages = self._messages(caplog, logging.ERROR, e.keymap.edit_config)
        assert any("keymap.editor failed" in m for m in messages)

    def test_without_app_control_logs_instead_of_crashing(self, engine, caplog):
        import logging
        e = engine(lambda keymap: None)
        assert e.keymap.app_control is None
        messages = self._messages(caplog, logging.WARNING, e.keymap.edit_config)
        assert any("No editor available" in m for m in messages)

    def test_deleted_config_is_recreated_before_opening(self, engine, tmp_path):
        import os
        e = engine(lambda keymap: None)
        e.keymap.app_control = FakeAppControl()
        # The fixture aliases template and config to one file; give the
        # recreation a real template to copy from.
        template = tmp_path / "template.py"
        template.write_text("def configure(keymap):\n    pass\n")
        e.keymap._template_path = str(template)
        os.remove(e.keymap._config_path)
        e.keymap.edit_config()
        assert os.path.exists(e.keymap._config_path)
        assert e.keymap.app_control.edited == [(e.keymap._config_path, None)]

    def test_reload_resets_editor_to_default(self, engine):
        e = engine(lambda keymap: None)
        e.keymap.editor = "CotEditor"
        e.keymap.configure()
        assert e.keymap.editor == ""

    def test_reload_config_is_an_alias_for_configure(self, engine, monkeypatch):
        e = engine(lambda keymap: None)
        calls = []
        monkeypatch.setattr(e.keymap, "configure", lambda: calls.append(1))
        e.keymap.reload_config()
        assert calls == [1]


class TestDescribeKeymap:
    """What an agent can check about a key binding without pressing it.

    The action loop closes because a run can be started and read back; nothing
    can press a key on the operator's behalf, and nothing should. This is the
    half that can be closed - the binding landed, in the table meant, and that
    table applies where they are standing.
    """

    def _config(self, keymap):
        kt = keymap.define_keytable(name="global", focus_path_pattern="*")
        kt["Fn-J"] = "Left"
        kt["U-A"] = "Right"
        chrome = keymap.define_keytable(name="chrome", app="Google Chrome")
        chrome["Fn-J"] = "Cmd-Left"
        multi = keymap.define_keytable(name="LEADER-X")
        multi["C"] = "Cmd-C"

    def _focused(self, engine, app="Google Chrome"):
        e = engine(self._config)
        e.keymap._focus = Focus(app_name=app, window_title="w",
                                class_name="", path="/App/Window")
        return e.keymap

    def test_it_reports_the_focus_path_a_pattern_is_written_against(self, engine):
        """Nothing else in the tool set hands that value back in a form you can
        paste into focus_path_pattern."""
        assert "/App/Window" in self._focused(engine).describe_keymap()

    def test_it_marks_which_tables_the_current_focus_activates(self, engine):
        text = self._focused(engine).describe_keymap()
        assert "  *  global" in text and "  *  chrome" in text

        text = self._focused(engine, app="Finder").describe_keymap()
        assert "  *  global" in text and "  *  chrome" not in text

    def test_a_multi_stroke_table_says_it_is_reached_from_a_key(self, engine):
        assert "LEADER-X: no condition" in self._focused(engine).describe_keymap()

    def test_keys_are_spelled_the_way_a_config_writes_them(self, engine):
        """str(KeyCondition) states the edge - D-Fn-J - because it is a
        diagnostic. Reporting that would not match the file it came from."""
        text = self._focused(engine).describe_keymap()
        assert "Fn-J -> 'Left'" in text and "D-Fn-J" not in text
        assert "U-A -> 'Right'" in text, "a key-up binding keeps its prefix"

    def test_the_override_case_is_visible(self, engine):
        """Two active tables binding one key is what configurations get wrong,
        so both rows have to be there to be compared."""
        text = self._focused(engine).describe_keymap()
        assert "Fn-J -> 'Left'" in text and "Fn-J -> 'Cmd-Left'" in text

    def test_it_stops_at_the_limit_rather_than_running_long(self, engine):
        text = self._focused(engine).describe_keymap(limit=1)
        assert "stopped at limit=1" in text


class TestActionRepr:
    """Every built-in action names itself. `describe_keymap` renders bindings
    with `{action!r}` and the candidate window lists them, so a class without
    one shows as `<module.Class object at 0x...>` in both."""

    def test_the_replay_actions_name_themselves(self):
        from keyhac.core.action import (
            PlaybackRecordedKeys, StartRecordingKeys, StopRecordingKeys,
            ToggleRecordingKeys,
        )
        assert repr(StartRecordingKeys()) == "StartRecordingKeys()"
        assert repr(StopRecordingKeys()) == "StopRecordingKeys()"
        assert repr(ToggleRecordingKeys()) == "ToggleRecordingKeys()"
        assert repr(PlaybackRecordedKeys()) == "PlaybackRecordedKeys()"

    def test_a_snippet_shows_its_format(self):
        from keyhac.actions import DateTimeSnippet
        assert repr(DateTimeSnippet("%Y-%m-%d")) == "DateTimeSnippet('%Y-%m-%d')"

    def test_no_bindable_built_in_falls_back_to_the_default_repr(self):
        """Anything a config can bind to a key and the window can list.

        "Bindable" is *instances are callable* - a class defining `__call__`
        somewhere in its own MRO - not `callable(cls)`, which is true of every
        class and sweeps in Keymap and the objects a config merely receives.
        """
        import keyhac
        missing = []
        for name in keyhac.__all__:
            obj = getattr(keyhac, name, None)
            if not isinstance(obj, type):
                continue
            instances_callable = any("__call__" in vars(base)
                                     for base in obj.__mro__ if base is not object)
            if not instances_callable:
                continue
            if obj.__module__.startswith("keyhac") and \
                    obj.__repr__ is object.__repr__:
                missing.append(name)
        assert missing == [], f"no __repr__: {missing}"
