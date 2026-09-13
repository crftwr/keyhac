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
    """The system-wide element lists AXFocusedApplication and
    AXFocusedUIElement among its attributes and then answers
    kAXErrorCannotComplete for both, while the frontmost application answers
    the same attribute instantly.

    Which callers it refuses was measured later, for issue #45: a process that
    has never created an NSApplication - every bare script and this test run -
    is refused instantly, and one that has (Keyhac) is answered.  So this
    fallback is the path tests and tools take, and the primary read is the one
    the shipped application takes; both have to work.
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


def test_the_focused_application_is_named_by_the_element_not_by_the_workspace(
        monkeypatch):
    """Issue #45's structural half.

    `get_focus` used to take `app_name` from NSWorkspace's frontmost
    application and the focused element from the system-wide AX read, with no
    cross-check, so one `Focus` could name one application and carry another's
    element - and `app_name` is what `define_keytable(app=...)` matches on.
    One resolution answers both halves now, so the two cannot disagree.
    """
    from keyhac.platform.mac import uielement as ue

    monkeypatch.setattr(ue, "_ax_get",
                        lambda element, attribute: "ax-app-ref"
                        if attribute == "AXFocusedApplication" else None)
    monkeypatch.setattr(ue, "NSWorkspace", _FakeWorkspace)
    monkeypatch.setattr(ue.AS, "AXUIElementCreateSystemWide", lambda: "sysw")
    monkeypatch.setattr(ue.AS, "AXUIElementGetPid",
                        lambda element, _: (0, 99)
                        if element == "ax-app-ref" else (-25204, 0))

    app, pid = ue.focused_application()
    assert app == "ax-app-ref"
    assert pid == 99, "the pid must come from the element, not from _FakeApp"


def test_the_fallback_names_the_process_it_actually_built_the_element_from(
        monkeypatch):
    """The other half of the same rule: when the system-wide read is refused
    and the front application supplies the element, the pid reported is that
    front application's - still the process the element belongs to."""
    from keyhac.platform.mac import uielement as ue

    monkeypatch.setattr(ue, "_ax_get", lambda element, attribute: None)
    monkeypatch.setattr(ue, "NSWorkspace", _FakeWorkspace)
    monkeypatch.setattr(ue.AS, "AXUIElementCreateSystemWide", lambda: "sysw")
    monkeypatch.setattr(ue.AS, "AXUIElementCreateApplication",
                        lambda pid: f"app-{pid}")

    app, pid = ue.focused_application()
    assert (app, pid) == ("app-4242", 4242)


def test_no_focused_application_at_all_is_a_pair_of_nones(monkeypatch):
    from keyhac.platform.mac import uielement as ue

    class _NoFrontApp(_FakeWorkspace):
        def frontmostApplication(self):
            return None

    monkeypatch.setattr(ue, "_ax_get", lambda element, attribute: None)
    monkeypatch.setattr(ue, "NSWorkspace", _NoFrontApp)
    monkeypatch.setattr(ue.AS, "AXUIElementCreateSystemWide", lambda: "sysw")

    assert ue.focused_application() == (None, None)


class _FakeRunningApp:
    def __init__(self, name):
        self._name = name

    def localizedName(self):
        return self._name


def test_the_app_name_is_the_name_of_the_process_holding_the_focus(monkeypatch):
    from keyhac.platform.mac import uielement as ue

    seen = []

    class _Lookup:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            seen.append(pid)
            return _FakeRunningApp("Claude")

    monkeypatch.setattr(ue, "NSRunningApplication", _Lookup)

    assert ue.app_name_of(99) == "Claude"
    assert seen == [99]


def test_a_pid_that_names_no_running_application_is_unnamed_not_borrowed(
        monkeypatch):
    """A focused process that is not an application - a helper, or one already
    gone - has no name a configuration could have been written against.  None
    matches no `app=` table; borrowing the frontmost application's name would
    put back the divergence issue #45 removed.
    """
    from keyhac.platform.mac import uielement as ue

    class _NoSuchApp:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            return None

    monkeypatch.setattr(ue, "NSRunningApplication", _NoSuchApp)

    assert ue.app_name_of(4242) is None
    assert ue.app_name_of(None) is None


# -- the content-access switch, read rather than written (issue #56) ---------

def test_a_chromium_bundle_is_recognised_by_its_renderer_helper(tmp_path):
    """Asked of the bundle because the accessibility tree cannot answer: an
    application that has not been asked exposes no web area, which is the same
    shape a native window has.  Both families ship a renderer helper - Electron
    directly under Frameworks, a Chromium browser inside its framework."""
    from keyhac.platform.mac.uielement import _ships_a_renderer

    electron = tmp_path / "Electron.app/Contents/Frameworks"
    (electron / "App Helper (Renderer).app").mkdir(parents=True)
    browser = tmp_path / "Browser.app/Contents/Frameworks"
    (browser / "Browser Framework.framework/Versions/153/Helpers/"
               "Browser Helper (Renderer).app").mkdir(parents=True)
    native = tmp_path / "Native.app/Contents/Frameworks"
    (native / "SomethingFramework.framework").mkdir(parents=True)

    assert _ships_a_renderer(str(tmp_path / "Electron.app")) is True
    assert _ships_a_renderer(str(tmp_path / "Browser.app")) is True
    assert _ships_a_renderer(str(tmp_path / "Native.app")) is False
    assert _ships_a_renderer(str(tmp_path / "Gone.app")) is False


def test_the_switch_reads_back_on_a_live_application():
    """Live: the value the flag reports is the value that was written, which is
    what lets describe_screen tell "not asked" from "asked, and the document is
    not there yet".  Read on Finder, which has the attribute like every Cocoa
    application and no content behind it, so nothing is disturbed."""
    import ApplicationServices as AS
    from keyhac.platform.mac.uielement import UIElement

    for name, pid in UIElement.get_running_applications():
        if name == "Finder":
            break
    else:
        pytest.skip("Finder is not running")

    app = UIElement(AS.AXUIElementCreateApplication(pid))
    assert app.get_manual_accessibility() in (True, False)
    assert app.is_chromium_application() is False
