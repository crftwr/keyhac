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
    terminal, which is an AXTextField. Since the sample rebinds `MOD1-K`
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


def shipped_action(keymap, name):
    """The template's binding whose function has this name, wherever it sits."""
    for table in keymap._all_keytables:
        for action in table.table.values():
            if getattr(action, "__name__", None) == name:
                return action
    raise AssertionError(f"the template no longer binds {name}()")


def test_the_ime_samples_run(shipped):
    """The IME samples read a tri-state and drive an input context; only
    running them says whether they still line up with the API."""
    from keyhac.platform.fake import FakeImeProvider

    shipped.ime_provider = FakeImeProvider(status=False)
    toggle = shipped_action(shipped, "toggle_ime")

    toggle()
    assert shipped.get_ime_status() is True
    toggle()
    assert shipped.get_ime_status() is False


def test_the_template_types_text_without_going_near_the_ime(shipped):
    """Literal text goes out as InputText, never as a send_key() batch.

    The distinction is not cosmetic: a batch is only *queued* for the
    application, so the "turn the IME off, send the keys, turn it back on"
    shape a config reaches for cannot work - the restore lands before the keys
    and "git status" arrives as "gいt" (doc/configuration.md). InputText
    injects the characters themselves, which the IME does not intercept, so
    there is nothing to turn off.
    """
    from keyhac.core.action import InputText

    typed = [action for table in shipped._all_keytables
             for action in table.table.values()
             if isinstance(action, InputText)]
    assert typed, "the template no longer shows InputText"


def test_the_ime_sample_leaves_an_unreadable_ime_alone(shipped, caplog):
    """None means "could not tell"; the toggle must say so rather than act."""
    from keyhac.platform.fake import FakeImeProvider

    shipped.ime_provider = FakeImeProvider(status=None)

    with caplog.at_level("WARNING"):
        shipped_action(shipped, "toggle_ime")()
    assert "No IME to toggle" in caplog.text
    assert shipped.get_ime_status() is None


def test_the_template_binds_no_key_twice(request, tmp_path, caplog,
                                         monkeypatch):
    """A second binding for a key silently replaces the first, and the
    template is long enough that it happened: the unified-window sample took
    Fn-Space and Fn-W, both of which were already spoken for. Nothing else
    would have said so - the IME test only noticed because the function it
    looks for vanished along with its binding.

    Recorded at *assignment* time, not from the finished tables: a dict cannot
    hold the same key twice, so by the time the template has run the loser is
    already gone and nothing is left to find.
    """
    from keyhac.core.key import KeyTable

    assignments = []
    original = KeyTable.__setitem__

    def record(self, key, value):
        # The table object itself, not id(): a freed table's id is handed
        # straight back to the next one, and two tables then look like one.
        assignments.append((self, str(key)))
        original(self, key, value)

    monkeypatch.setattr(KeyTable, "__setitem__", record)

    keymap = Keymap(FakeInputHook("ansi"), FakeFocusProvider(), "mac",
                    config_path=str(tmp_path / "config.py"))
    keymap._clipboard_history = ClipboardHistory(
        FakeClipboardProvider(), str(tmp_path / "clipboard.json"))
    with caplog.at_level("ERROR"):
        keymap.configure()
    assert not caplog.records, caplog.text

    seen = {}
    for table, key in assignments:
        keys = seen.setdefault(id(table), (table, set()))[1]
        assert key not in keys, \
            f"{key} is bound twice in key table {table.name!r}"
        keys.add(key)
    assert len(assignments) > 30, "the template stopped binding anything"
