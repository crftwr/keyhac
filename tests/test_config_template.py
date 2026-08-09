"""The `config.py` shipped to every new installation, loaded as a config.

`keyhac/_config.py` is copied to `~/.keyhac/config.py` on first run and is the
only Keyhac most people will ever read.  Nothing exercised it, so a sample that
had stopped being true stayed shipped -- and a sample is read as a
recommendation, not as an illustration.
"""

import pytest

from keyhac.core.clipboard_history import ClipboardHistory
from keyhac.core.keymap import Keymap
from keyhac.platform.base import Focus
from keyhac.platform.fake import FakeInputHook, FakeFocusProvider


class FakeElement:
    """Just enough to answer the one question a focus condition asks."""

    def __init__(self, **attributes):
        self.attributes = attributes

    def get_attribute_value(self, name):
        return self.attributes.get(name)


class FakeClipboardProvider:
    def get_text(self):
        return ""

    def set_text(self, text):
        pass


@pytest.fixture(params=["mac", "windows"])
def shipped(request, tmp_path, caplog):
    """A Keymap that loaded the real template, on each platform in turn.

    Wired the way `main()` wires one, minus the OS: the template configures
    clipboard history, so a Keymap without it fails the load and every
    assertion below would pass against a config that never ran.
    """
    # No template_path: the default is `keyhac/_config.py`, which is the file
    # under test - naming it here would let the two drift apart.
    keymap = Keymap(FakeInputHook("ansi"), FakeFocusProvider(), request.param,
                    config_path=str(tmp_path / "config.py"))
    keymap._clipboard_history = ClipboardHistory(
        FakeClipboardProvider(), str(tmp_path / "clipboard.json"))

    with caplog.at_level("ERROR"):
        keymap.configure()

    # configure() reports a broken config to the console and returns, so this
    # is the difference between "the sample loads" and "the sample half-loaded
    # and the tests below found nothing to disagree with".
    assert not caplog.records, caplog.text
    return keymap


def custom_conditions(keymap):
    return [condition for condition, _table in keymap._keytable_list
            if condition.custom_condition_func is not None]


def focus_on(element, app_name="SomeEditor"):
    return Focus(app_name=app_name, pid=1, window_title="Main",
                 path="/App/Window", element=element, native=element)


def test_the_template_loads_on_both_platforms(shipped):
    """It is executed for the first time on somebody's first run."""
    assert shipped._keytable_list, "the sample defined no key tables"


def test_a_text_area_is_not_a_terminal(shipped):
    """Issue #46, and it shipped inverted.

    The sample's `is_terminal` fell back to `role in ("AXTextArea",
    "Document")` when the application name did not match. `AXTextArea` means
    "multi-line text control", not "terminal": it caught the VS Code editor and
    every chat box on the machine, and *missed* VS Code's own integrated
    terminal, which is an AXTextField. Since the sample rebinds `LEADER-K`
    inside that table, a fresh install turned `Fn-K` into `Ctrl-K` everywhere
    text is typed and left it alone in an actual terminal.
    """
    editor = focus_on(FakeElement(AXRole="AXTextArea", ControlType="Document"))
    for condition in custom_conditions(shipped):
        assert not condition.check(editor), \
            "a text area still satisfies a sample condition"


def test_a_named_terminal_still_matches(shipped):
    """Removing the wrong half must not remove the working half."""
    terminal = focus_on(FakeElement(), app_name="Terminal")
    assert any(condition.check(terminal)
               for condition in custom_conditions(shipped)), \
        "no sample condition recognises Terminal any more"
