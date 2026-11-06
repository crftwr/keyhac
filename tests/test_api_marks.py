"""`@preview`, `@deprecated` and the load-time check on callbacks (#278)."""

import keyhac
from keyhac.core import api_marks
from keyhac.core.focus import FocusCondition
from keyhac.core.source import CallableSource


def _outside(function, *args):
    """Call `function` as code outside Keyhac does."""
    return eval("function(*args)", {"__name__": "user_config", "function": function,
                                    "args": args})


def test_the_registries_are_published():
    assert keyhac.PREVIEW_APIS is api_marks.PREVIEW_APIS
    assert keyhac.DEPRECATED_APIS is api_marks.DEPRECATED_APIS


def test_nothing_existing_is_marked():
    # The marks start empty: everything shipped so far is promised.
    assert not api_marks.PREVIEW_APIS
    assert not api_marks.DEPRECATED_APIS


def test_preview_opens_the_docstring_and_registers(monkeypatch):
    monkeypatch.setattr(api_marks, "PREVIEW_APIS", set())

    class Thing:
        @api_marks.preview
        def method(self):
            """Does it."""

    name = "test_preview_opens_the_docstring_and_registers.<locals>.Thing.method"
    assert api_marks.PREVIEW_APIS == {name}
    assert Thing.method.__doc__.startswith(api_marks.PREVIEW_NOTE)
    assert "Does it." in Thing.method.__doc__
    assert api_marks.is_preview(name)
    assert not api_marks.is_preview("Other.method")


def test_a_class_mark_covers_its_members(monkeypatch):
    monkeypatch.setattr(api_marks, "PREVIEW_APIS", {"Thing"})
    monkeypatch.setattr(api_marks, "DEPRECATED_APIS", {"Old": "use New"})
    assert api_marks.is_preview("Thing.method")
    assert api_marks.is_deprecated("Old.method") == "use New"
    assert api_marks.is_deprecated("Thing.method") is None


class TestDeprecated:

    def _isolated(self, monkeypatch):
        warnings = []
        monkeypatch.setattr(api_marks, "DEPRECATED_APIS", {})
        monkeypatch.setattr(api_marks, "_recorded", set())
        monkeypatch.setattr(api_marks.logger, "warning", warnings.append)
        return warnings

    def test_it_keeps_working_and_warns_once_per_caller(self, monkeypatch):
        warnings = self._isolated(monkeypatch)

        @api_marks.deprecated("use new_one")
        def old_one(value):
            """Doubles."""
            return value * 2

        assert _outside(old_one, 2) == 4
        assert _outside(old_one, 3) == 6
        assert len(warnings) == 1
        assert "user_config" in warnings[0] and "use new_one" in warnings[0]
        assert old_one.__doc__.startswith("Deprecated — use new_one.")
        assert "Doubles." in old_one.__doc__

    def test_keyhacs_own_calls_are_not_reported(self, monkeypatch):
        warnings = self._isolated(monkeypatch)
        api_marks._record_use("Thing.old", "keyhac.core.keymap", "use new")
        api_marks._record_use("Thing.old", "keyhac", "use new")
        assert not warnings

    def test_a_class_warns_when_constructed(self, monkeypatch):
        warnings = self._isolated(monkeypatch)

        @api_marks.deprecated("use New")
        class Old:
            def __init__(self, value):
                self.value = value

        assert _outside(Old, 5).value == 5
        assert len(warnings) == 1

    def test_it_works_under_property(self, monkeypatch):
        self._isolated(monkeypatch)

        class Thing:
            @property
            @api_marks.deprecated("use other")
            def old(self):
                return 7

        assert Thing().old == 7
        assert any(name.endswith("Thing.old") for name in api_marks.DEPRECATED_APIS)


class TestCallbackCheck:

    def _warnings(self, monkeypatch):
        warnings = []
        monkeypatch.setattr(api_marks.logger, "warning", warnings.append)
        return warnings

    def test_a_callback_that_cannot_take_its_arguments_is_named(self, monkeypatch):
        warnings = self._warnings(monkeypatch)

        def no_arguments():
            pass

        FocusCondition(custom_condition_func=no_arguments)
        assert len(warnings) == 1
        assert "custom_condition_func" in warnings[0] and "no_arguments" in warnings[0]

    def test_on_chosen_takes_two(self, monkeypatch):
        warnings = self._warnings(monkeypatch)

        def one(candidate):
            pass

        CallableSource(list, on_chosen=one)
        assert len(warnings) == 1 and "on_chosen" in warnings[0]

    def test_what_fits_says_nothing(self, monkeypatch):
        warnings = self._warnings(monkeypatch)

        class Bound:
            def two(self, candidate, flags):
                pass

        CallableSource(list, on_chosen=Bound().two)
        CallableSource(list, on_chosen=lambda *args: None)
        CallableSource(list, on_chosen=None)
        FocusCondition(custom_condition_func=lambda focus, extra=1: True)
        api_marks.warn_unless_callable_with(print, 2, "uninspectable or not")
        assert not warnings
