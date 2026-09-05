"""macOS AX value conversion (issue #6: NSArray attributes such as AXWindows
were falling through _from_ax's isinstance checks to the str() fallback)."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def test_from_ax_nsarray():
    from Foundation import NSMutableArray
    from keyhac.platform.mac.uielement import _from_ax

    array = NSMutableArray.array()
    array.addObject_("x")
    array.addObject_(2)
    assert _from_ax(array) == ["x", 2]


def test_from_ax_nsdictionary():
    from Foundation import NSDictionary
    from keyhac.platform.mac.uielement import _from_ax

    d = NSDictionary.dictionaryWithDictionary_({"k": "v"})
    assert _from_ax(d) == {"k": "v"}


@pytest.mark.parametrize("type_name,value", [
    ("point", (1.0, 2.0)),
    ("size", (3.0, 4.0)),
    ("rect", (5.0, 6.0, 7.0, 8.0)),
    ("range", (9, 10)),          # CFRange arrives as a tuple, not a struct
])
def test_ax_value_round_trip(type_name, value):
    """Every AXValue type survives _to_ax -> _from_ax unchanged.

    "range" is the one that did not: AXSelectedTextRange raised AttributeError
    instead of returning (location, length), which took the caret vocabulary
    with it.
    """
    from keyhac.platform.mac.uielement import _from_ax, _to_ax

    assert _from_ax(_to_ax(type_name, value)) == value


def test_from_ax_scalars_bridge():
    from Foundation import NSNumber, NSString
    from keyhac.platform.mac.uielement import _from_ax

    assert _from_ax(NSString.stringWithString_("s")) == "s"
    assert _from_ax(NSNumber.numberWithBool_(True)) == True  # noqa: E712 (bridged NSNumber)
    assert _from_ax(NSNumber.numberWithInt_(7)) == 7


def _answers(selection, characters, parameterized):
    """A UIElement whose AX reads come from a dict, so the caret logic can be
    tested without an application to read from."""
    from keyhac.platform.mac.uielement import UIElement

    class _Fake(UIElement):
        def __init__(self):
            super().__init__(None)

        def get_attribute_value(self, name):
            return {"AXSelectedTextRange": selection,
                    "AXNumberOfCharacters": characters}.get(name)

        def get_parameterized_attribute_value(self, name, type_name, value):
            key = (name, tuple(value) if isinstance(value, (tuple, list))
                   else value)
            return parameterized.get(key)

    return _Fake()


class TestACaretPastTheLastCharacter:
    """TextEdit at the end of a document that ends in a newline.

    Every answer names the line the *newline* is on; the caret is on the empty
    line under it. Measured: the insertion point says (101, 497, 0, 14), the
    newline at offset 62 says (101, 497, 576, 14), and the caret's line agrees
    with them - while the caret is at (101, 511). The balloon covered the line
    being typed on, and only on the last one."""

    ENDS_IN_A_NEWLINE = {
        ("AXBoundsForRange", (63, 1)): None,
        ("AXBoundsForRange", (63, 0)): (101.0, 497.0, 0.0, 14.0),
        ("AXBoundsForRange", (62, 1)): (101.0, 497.0, 576.0, 14.0),
        ("AXStringForRange", (62, 1)): "\n",
        ("AXLineForIndex", 63): 8,
        ("AXRangeForLine", 8): (62, 1),
    }

    def test_the_caret_is_on_the_line_below_the_newline(self):
        assert _answers((63, 0), 63, self.ENDS_IN_A_NEWLINE).get_caret_rect() \
            == (101.0, 511.0, 0.0, 14.0)

    def test_after_an_ordinary_character_it_is_the_trailing_edge(self):
        element = _answers((10, 0), 10, {
            ("AXBoundsForRange", (10, 1)): None,
            ("AXBoundsForRange", (10, 0)): (200.0, 300.0, 0.0, 14.0),
            ("AXBoundsForRange", (9, 1)): (250.0, 300.0, 8.0, 14.0),
            ("AXStringForRange", (9, 1)): "o",
        })
        assert element.get_caret_rect() == (258.0, 300.0, 0.0, 14.0)

    def test_an_empty_document_has_no_character_to_ask(self):
        element = _answers((0, 0), 0, {
            ("AXBoundsForRange", (0, 1)): None,
            ("AXBoundsForRange", (0, 0)): (101.0, 400.0, 0.0, 14.0),
        })
        assert element.get_caret_rect() == (101.0, 400.0, 0.0, 14.0)

    def test_the_character_at_the_caret_still_wins_when_there_is_one(self):
        element = _answers((5, 0), 20, {
            ("AXBoundsForRange", (5, 1)): (150.0, 300.0, 8.0, 14.0),
            ("AXBoundsForRange", (5, 0)): (150.0, 286.0, 0.0, 14.0),
        })
        assert element.get_caret_rect() == (150.0, 300.0, 8.0, 14.0)


# -- where the keyboard focus is ---------------------------------------------

class _FakeApp:
    def processIdentifier(self):
        return 4242


class _FakeWorkspace:
    @classmethod
    def sharedWorkspace(cls):
        return cls()

    def frontmostApplication(self):
        return _FakeApp()


def test_the_focus_resolution_asks_the_front_app_when_system_wide_will_not_say(
        monkeypatch):
    """Measured on macOS 26.6.2: the system-wide element lists
    AXFocusedApplication and AXFocusedUIElement among its attributes and then
    answers kAXErrorCannotComplete for both, every time, while the frontmost
    application answers the same attribute instantly.
    """
    from keyhac.platform.mac import uielement as ue

    asked = []

    def fake_ax_get(element, attribute):
        asked.append((element, attribute))
        if element == "app-ref" and attribute == "AXFocusedUIElement":
            return "focused-ref"
        return None                      # the system-wide element says nothing

    monkeypatch.setattr(ue, "_ax_get", fake_ax_get)
    monkeypatch.setattr(ue, "NSWorkspace", _FakeWorkspace)
    monkeypatch.setattr(ue.AS, "AXUIElementCreateSystemWide", lambda: "sysw")
    monkeypatch.setattr(ue.AS, "AXUIElementCreateApplication", lambda pid: "app-ref")

    node = ue.focused_element()
    assert isinstance(node, ue.UIElement) and node._ref == "focused-ref"
    assert ("sysw", "AXFocusedApplication") in asked


def test_no_readable_focus_is_none_rather_than_a_guess(monkeypatch):
    from keyhac.platform.mac import uielement as ue

    monkeypatch.setattr(ue, "_ax_get", lambda element, attribute: None)
    monkeypatch.setattr(ue, "NSWorkspace", _FakeWorkspace)
    monkeypatch.setattr(ue.AS, "AXUIElementCreateSystemWide", lambda: "sysw")
    monkeypatch.setattr(ue.AS, "AXUIElementCreateApplication", lambda pid: "app-ref")

    assert ue.focused_element() is None


def test_the_predicates_never_fall_back_to_the_elements_own_flag(monkeypatch):
    """The bug this replaced.

    Both predicates used to fall back to this element's AXFocused when the
    focus could not be read.  That flag answers has_focus()'s question and not
    contains_focus()'s, so every container that really did hold the focus
    reported False - measured against a focused TextEdit AXTextArea, where the
    AXScrollArea and AXWindow above it both said False.
    """
    from keyhac.platform.mac import uielement as ue

    monkeypatch.setattr(ue, "focused_element", lambda *args: None)
    monkeypatch.setattr(ue.UIElement, "get_attribute_value",
                        lambda self, name: True)      # would have claimed focus

    element = ue.UIElement("ref")
    assert element.has_focus() is False
    assert element.contains_focus() is False
