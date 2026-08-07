"""Writing into the UI (keyhac/core/fill.py).

Hermetic on every platform: the fake element implements the same small
protocol the real ones do, and the fake clipboard stands in for the provider.
The behaviours pinned here are the ones that failed live while this was being
written - the clipboard restored before the application had read it, and a
setup error reported as "this mechanism does not work".
"""

import pytest

from keyhac.core import fill
from keyhac.core.fill import (
    FillFailed, preserve_clipboard, press, read_value, set_checked, set_text,
)


class FakeClipboard:
    def __init__(self, text=None):
        self.text = text
        self.writes = []

    def get_text(self):
        return self.text

    def set_text(self, s):
        self.text = s
        self.writes.append(s)


class FakeField:
    """A field whose mechanisms can be made to fail independently."""

    def __init__(self, value="", focusable=True, accepts_set_value=True,
                 role="AXTextField", actions=("AXPress",)):
        self.value = value
        self.focusable = focusable
        self.accepts_set_value = accepts_set_value
        self.role = role
        self.focused = False
        self.presses = 0
        self._actions = list(actions)

    def describe(self):
        return {"role": self.role, "name": None, "value": self.value,
                "identifier": None, "rect": None}

    def children(self):
        return []

    def identity_key(self):
        return id(self)

    def set_focus(self):
        self.focused = self.focusable
        return self.focused

    def set_value(self, text):
        if self.accepts_set_value:
            self.value = text
        return self.accepts_set_value

    def get_action_names(self):
        return list(self._actions)

    def perform_action(self, name):
        self.presses += 1
        if self.role == "AXCheckBox":
            self.value = 0 if self.value else 1


@pytest.fixture
def wired(engine, monkeypatch):
    """A Keymap with a fake clipboard, and typing captured rather than sent."""
    fixture = engine(lambda keymap: None)
    keymap = fixture.keymap
    clipboard = FakeClipboard("what the user had copied")
    monkeypatch.setattr(type(keymap), "clipboard",
                        property(lambda self: clipboard), raising=False)
    return keymap, clipboard


# -- clipboard ---------------------------------------------------------------

def test_preserve_clipboard_restores(wired):
    _keymap, clipboard = wired
    with preserve_clipboard():
        clipboard.set_text("scratch")
        assert clipboard.get_text() == "scratch"
    assert clipboard.get_text() == "what the user had copied"


def test_preserve_clipboard_restores_after_an_exception(wired):
    _keymap, clipboard = wired
    with pytest.raises(ValueError):
        with preserve_clipboard():
            clipboard.set_text("scratch")
            raise ValueError("boom")
    assert clipboard.get_text() == "what the user had copied"


# -- set_text ----------------------------------------------------------------

def test_refuses_to_write_when_focus_does_not_land(wired):
    field = FakeField(focusable=False)
    with pytest.raises(FillFailed, match="could not focus"):
        set_text(field, "REC-001")
    assert field.value == ""


def test_set_value_method_writes_and_verifies(wired):
    field = FakeField()
    assert set_text(field, "REC-001", methods=("set_value",)) == "set_value"
    assert field.value == "REC-001"


def test_falls_back_to_the_next_mechanism(wired, monkeypatch):
    """A mechanism that writes nothing must not end the attempt."""
    field = FakeField(accepts_set_value=False)
    calls = []

    def fake_paste(text, clear, confirm, settle=False):
        calls.append("paste")
        field.value = text
        return confirm()

    monkeypatch.setattr(fill, "_paste", fake_paste)
    assert set_text(field, "REC-001", methods=("set_value", "paste"),
                    timeout=0.3) == "paste"
    assert calls == ["paste"]
    assert field.value == "REC-001"


def test_failure_says_why_each_mechanism_failed(wired, monkeypatch):
    """A setup error used to be swallowed and reported as "does not work",
    which sent a live investigation in the wrong direction for an hour."""
    field = FakeField(accepts_set_value=False)

    def exploding_paste(text, clear, confirm, settle=False):
        raise RuntimeError("init_key_names() has not been called yet")

    monkeypatch.setattr(fill, "_paste", exploding_paste)
    with pytest.raises(FillFailed) as caught:
        set_text(field, "REC-001", methods=("set_value", "paste"), timeout=0.2)

    message = str(caught.value)
    assert "set_value: wrote nothing readable" in message
    assert "init_key_names" in message
    assert caught.value.attempted == ("set_value", "paste")


def test_verify_off_accepts_whatever_happened(wired):
    field = FakeField(accepts_set_value=False)
    assert set_text(field, "REC-001", methods=("set_value",), verify=False) \
        == "set_value"


def test_unverified_paste_holds_the_clipboard_before_restoring(wired, monkeypatch):
    """The read-back is also what says the target has taken the pasteboard.

    With verify=False there is none, and the restore used to go out in the same
    breath as Ctrl-V: the field then received whatever had been on the
    clipboard *before*, which reads as a successful paste of the wrong text.
    Seen live on Windows 11 (Notepad, tools/uia_pass.py) - the document ended
    up holding a shell command copied an hour earlier.
    """
    _keymap, clipboard = wired
    field = FakeField(accepts_set_value=False)
    events = []

    real_set_text = clipboard.set_text

    def record(text):
        events.append(("clipboard", text))
        real_set_text(text)

    monkeypatch.setattr(clipboard, "set_text", record)
    monkeypatch.setattr(fill.time, "sleep",
                        lambda seconds: events.append(("sleep", seconds)))

    assert set_text(field, "REC-001", methods=("paste",), verify=False) == "paste"
    assert events == [("clipboard", "REC-001"),
                      ("sleep", fill.PASTE_SETTLE),
                      ("clipboard", "what the user had copied")]


def test_verified_paste_does_not_pay_the_settle(wired, monkeypatch):
    """confirm() is the better signal, so the fixed delay is not also spent."""
    slept = []
    monkeypatch.setattr(fill.time, "sleep", lambda seconds: slept.append(seconds))
    assert fill._paste("REC-001", clear=True, confirm=lambda: True) is True
    assert slept == []


def test_read_value(wired):
    assert read_value(FakeField(value="x")) == "x"


# -- checkbox ----------------------------------------------------------------

def test_checkbox_is_read_before_it_is_pressed(wired):
    """Pressing blindly toggles, so a rerun would undo the first run."""
    box = FakeField(value=0, role="AXCheckBox", actions=("AXPress",))
    assert set_checked(box, True) is True         # pressed
    assert box.value == 1 and box.presses == 1
    assert set_checked(box, True) is False        # already right: no press
    assert box.presses == 1
    assert set_checked(box, False) is True
    assert box.value == 0 and box.presses == 2


def test_checkbox_accepts_the_platforms_various_truths(wired):
    for value, checked in (("1", True), ("true", True), ("on", True),
                           ("0", False), (2, False), (None, False)):
        box = FakeField(value=value, role="AXCheckBox")
        assert fill._is_checked(box) is checked, value


# -- press -------------------------------------------------------------------

def test_press_uses_whichever_action_the_platform_offers(wired):
    for actions in (("AXPress",), ("Invoke",), ("Toggle",)):
        button = FakeField(actions=actions)
        press(button)
        assert button.presses == 1


def test_press_reports_an_element_that_cannot_be_pressed(wired):
    with pytest.raises(FillFailed, match="no press action"):
        press(FakeField(actions=()))
