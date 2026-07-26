"""FocusCondition matching."""

from keyhac.core.focus import FocusCondition
from keyhac.platform.base import Focus


FOCUS = Focus(app_name="Code", pid=42, window_title="main.py - myproject",
              class_name="Chrome_WidgetWin_1",
              path="/AXApplication(Code)/AXWindow(main.py - myproject)/AXTextArea()")


class TestApp:

    def test_exact(self):
        assert FocusCondition(app="Code").check(FOCUS)

    def test_case_insensitive(self):
        assert FocusCondition(app="code").check(FOCUS)

    def test_wildcard(self):
        assert FocusCondition(app="Co*").check(FOCUS)

    def test_alternation(self):
        assert FocusCondition(app="Terminal|Code|iTerm2").check(FOCUS)
        assert not FocusCondition(app="Terminal|iTerm2").check(FOCUS)

    def test_exe_suffix_tolerated(self):
        win_focus = Focus(app_name="notepad", window_title="x")
        assert FocusCondition(app="notepad.exe").check(win_focus)
        assert FocusCondition(app="NOTEPAD").check(win_focus)

    def test_no_focus(self):
        assert not FocusCondition(app="Code").check(None)


class TestTitleAndClass:

    def test_title_wildcard(self):
        assert FocusCondition(title="*myproject*").check(FOCUS)
        assert not FocusCondition(title="*otherproject*").check(FOCUS)

    def test_class_name(self):
        assert FocusCondition(class_name="Chrome_WidgetWin_*").check(FOCUS)

    def test_class_name_absent_on_mac(self):
        mac_focus = Focus(app_name="Code", window_title="t", class_name=None, path="/x")
        assert not FocusCondition(class_name="Edit").check(mac_focus)


class TestPathAndCustom:

    def test_focus_path_pattern(self):
        assert FocusCondition(focus_path_pattern="*/AXTextArea()").check(FOCUS)
        assert not FocusCondition(focus_path_pattern="*/AXButton()").check(FOCUS)

    def test_star_matches_any_focus(self):
        assert FocusCondition(focus_path_pattern="*").check(FOCUS)

    def test_star_requires_some_focus(self):
        assert not FocusCondition(focus_path_pattern="*").check(None)

    def test_custom_condition(self):
        assert FocusCondition(custom_condition_func=lambda f: f.pid == 42).check(FOCUS)
        assert not FocusCondition(custom_condition_func=lambda f: f.pid == 1).check(FOCUS)

    def test_custom_condition_exception_is_contained(self):
        def boom(focus):
            raise RuntimeError("bug in user condition")
        assert not FocusCondition(custom_condition_func=boom).check(FOCUS)

    def test_and_combination(self):
        assert FocusCondition(app="Code", title="*myproject*").check(FOCUS)
        assert not FocusCondition(app="Code", title="*nope*").check(FOCUS)
