"""The action-facing API (keymap.ui and UINode's methods).

Hermetic: the fakes implement the same small protocol the platform elements do,
so this runs on any OS. What it pins is the *shape* the reorganisation was for
- one entry point, methods on nodes, three importable names - plus the two
behaviours that are easy to lose in a refactor: every method dispatches to the
loop thread itself, and a node is a snapshot rather than a live view.
"""

import threading

import pytest

from keyhac.core.uitree import UINode
from keyhac.core.wait import WaitTimeout


class FakeElement:
    def __init__(self, role=None, name=None, value=None, identifier=None,
                 children=(), key=None, text=None):
        self._describe = {"role": role, "name": name, "value": value,
                          "identifier": identifier, "rect": None}
        self.kids = list(children)
        self._key = key or id(self)
        self._text = text
        self.pressed = 0
        self.focused = False

    def describe(self):
        return dict(self._describe)

    def children(self):
        return list(self.kids)

    def identity_key(self):
        return self._key

    def get_text(self):
        return self._text

    def get_line_at_caret(self):
        return (self._text or "").split("\n")[0] or None

    def get_selection(self):
        return ""

    def get_action_names(self):
        return ["AXPress"]

    def perform_action(self, name):
        self.pressed += 1

    def set_focus(self):
        self.focused = True
        return True


class FakeWindow:
    """What keymap.find_window / list_windows hand back."""

    def __init__(self, element, app_name="TestApp", title="Main"):
        self.element = element
        self.app_name = app_name
        self.title = title
        self.class_name = None
        self.pid = 1


@pytest.fixture(autouse=True)
def no_leftover_dispatcher():
    yield
    from keyhac.core.keymap import Keymap
    keymap = Keymap.get_instance()
    if keymap is not None:
        keymap.set_main_thread_dispatcher(None)


@pytest.fixture
def ui(engine):
    """keymap.ui, wired to one fake window."""
    fixture = engine(lambda keymap: None)
    element = FakeElement("Window", name="Main", key="w", children=[
        FakeElement("AXButton", name="Save", identifier="save", key="b"),
        FakeElement("AXCheckBox", name="Archived", identifier="arch", value=0,
                    key="c"),
        FakeElement("AXTextArea", name="Body", key="t",
                    text="first line\nsecond line"),
    ])
    window = FakeWindow(element)

    class Provider:
        def list_windows(self):
            return [window]

        def get_active_window(self):
            return window

        def find_window(self, app=None, title=None, class_name=None):
            from keyhac.core.focus import match_window_fields
            for candidate in self.list_windows():
                if match_window_fields(candidate, app=app, title=title,
                                       class_name=class_name):
                    return candidate
            return None

    fixture.keymap.window_provider = Provider()
    return fixture.keymap.ui, element


# -- the cut ----------------------------------------------------------------

def test_only_three_action_names_are_importable():
    """The reorganisation's whole point: a config's namespace does not grow a
    `press`, a `focus` and a `find_element` that only mean something inside an
    action."""
    import keyhac

    action_names = {"UINode", "WaitTimeout", "FillFailed"}
    assert action_names <= set(keyhac.__all__)
    for gone in ("get_ui_tree", "find_element", "find_elements", "format_tree",
                 "wait_for", "wait_for_element", "wait_until_gone",
                 "wait_for_stable", "set_text", "set_checked", "press",
                 "focus", "preserve_clipboard"):
        assert not hasattr(keyhac, gone), f"{gone} is still a global"


def test_a_threaded_action_reaches_the_api_without_imports(engine):
    from keyhac.core.action import ThreadedAction

    engine(lambda keymap: None)
    action = ThreadedAction()
    assert action.keymap is not None
    assert action.ui is action.keymap.ui


def test_the_same_ui_object_every_time(ui):
    api, _element = ui
    assert api is api._keymap.ui


# -- entry points -----------------------------------------------------------

def test_window_lookup_returns_a_node(ui):
    api, _element = ui
    window = api.window(app="TestApp")
    assert isinstance(window, UINode)
    assert window.name == "Main"


def test_window_lookup_matches_like_define_keytable(ui):
    api, _element = ui
    assert api.window(app="testapp") is not None        # case-insensitive
    assert api.window(app="Other|TestApp") is not None  # alternation
    assert api.window(app="Nope") is None


def test_windows_returns_all_matches(ui):
    api, _element = ui
    assert len(api.windows(app="TestApp")) == 1
    assert api.windows(app="Nope") == []


# -- focused() asks, it does not remember (issue #44) ------------------------
#
# This used to read `keymap.focus`, which is a snapshot taken while a key was
# being dispatched. An action that closed a window and waited for focus to
# land somewhere else never saw it move: it kept being handed the destroyed
# element, or the application that no longer had a window, and polling did not
# help because polling produces no keystrokes.

def test_focused_asks_the_platform_each_time(ui):
    api, _element = ui
    provider = api._keymap._focus_provider

    first, second = FakeElement("AXTextArea", name="Editor", key="e"), \
        FakeElement("AXTextField", name="Search", key="s")
    provider.get_focused_element = lambda: provider.element

    provider.element = first
    assert api.focused().name == "Editor"

    provider.element = second                  # focus moved; no key was typed
    assert api.focused().name == "Search", \
        "focused() answered from a snapshot instead of asking"


def test_focused_does_not_read_the_keypress_snapshot(ui):
    """A stale element that fails every attribute read is not an answer.

    None is: the caller can branch on it, and an action waiting for focus to
    settle keeps waiting instead of driving a window that is gone. So the
    snapshot is loaded here with exactly what the report saw - the destroyed
    window still sitting in `keymap.focus` - and focused() must not reach it.
    """
    api, _element = ui
    keymap = api._keymap
    keymap._focus = keymap._focus_provider.get_focus()
    keymap._focus.element = FakeElement("AXWindow", name="Destroyed", key="x")
    keymap._focus_provider.get_focused_element = lambda: None

    assert api.focused() is None


def test_the_default_provider_reads_the_live_focus(engine):
    """Every provider gets this for free; only the cost differs.

    macOS and Windows override it to skip building the focus *path* - a parent
    walk of cross-process round trips that nothing here reads.
    """
    from keyhac.platform.base import Focus, FocusProvider

    element = FakeElement("AXTextArea", name="Body", key="b")

    class Minimal(FocusProvider):
        def get_focus(self):
            return Focus(app_name="App", pid=1, element=element)

    assert Minimal().get_focused_element() is element


def test_node_wraps_a_platform_element_shallowly(ui):
    api, element = ui
    node = api.node(element)
    assert node.role == "Window"
    assert node.children == []          # shallow: nothing below was read
    assert api.node(None) is None
    assert api.node(node) is node       # idempotent


# -- node methods -----------------------------------------------------------

def test_find_and_find_all(ui):
    api, _element = ui
    window = api.window(app="TestApp")
    assert window.find(identifier="save").name == "Save"
    assert window.find(identifier="nope") is None
    assert len(window.find_all(role="AXButton|AXCheckBox")) == 2


def test_find_takes_the_walk_bounds(ui):
    api, element = ui
    element.kids.append(FakeElement("AXGroup", key="g1", children=[
        FakeElement("AXGroup", key="g2", children=[
            FakeElement("AXButton", identifier="deep", key="d")])]))
    window = api.window(app="TestApp")
    assert window.find(identifier="deep", max_depth=1) is None
    assert window.find(identifier="deep", max_depth=5).identifier == "deep"


def test_find_all_takes_the_walk_bounds(ui):
    api, _element = ui
    window = api.window(app="TestApp")
    everything = window.find_all(role="*")
    cut = window.find_all(role="*", max_nodes=2)
    assert 0 < len(cut) < len(everything)


def test_wait_for_takes_the_walk_bounds(ui):
    api, element = ui
    element.kids.append(FakeElement("AXGroup", key="g", children=[
        FakeElement("AXSheet", identifier="modal", key="m")]))
    window = api.window(app="TestApp")
    assert window.wait_for(identifier="modal", max_depth=5,
                           timeout=1).role == "AXSheet"
    with pytest.raises(WaitTimeout):
        window.wait_for(identifier="modal", max_depth=1, timeout=0.2)


def test_reread_gives_a_fresh_snapshot(ui):
    api, element = ui
    window = api.window(app="TestApp")
    assert window.find(identifier="added") is None
    element.kids.append(FakeElement("AXButton", identifier="added", key="new"))
    # The old node is a snapshot and does not update...
    assert not any(n.identifier == "added" for n in window.children)
    # ...but re-reading sees it.
    assert any(n.identifier == "added" for n in window.reread().children)


def test_text_layer_methods(ui):
    api, _element = ui
    body = api.window(app="TestApp").find(role="AXTextArea")
    assert body.read_text() == "first line\nsecond line"
    assert body.line_at_caret() == "first line"
    assert body.selection() == ""


def test_press_and_focus(ui):
    api, element = ui
    button = api.window(app="TestApp").find(identifier="save")
    button.press()
    assert element.kids[0].pressed == 1
    assert button.focus() is True


def test_set_checked_reads_before_pressing(ui, monkeypatch):
    api, element = ui
    box = api.window(app="TestApp").find(identifier="arch")

    def toggle(name):
        element.kids[1].pressed += 1
        element.kids[1]._describe["value"] = \
            0 if element.kids[1]._describe["value"] else 1

    monkeypatch.setattr(element.kids[1], "perform_action", toggle)
    assert box.set_checked(True) is True
    assert element.kids[1].pressed == 1
    assert box.set_checked(True) is False       # already right: no press
    assert element.kids[1].pressed == 1


def test_dump_renders_the_subtree(ui):
    api, _element = ui
    text = api.window(app="TestApp").reread().dump()
    assert "AXButton" in text and "Save" in text


# -- threading --------------------------------------------------------------

def test_every_read_dispatches_to_the_loop_thread(ui):
    """The ergonomic the reorganisation bought: an action's body has no thread
    ceremony, because each method hops for itself."""
    api, _element = ui
    ran_on = []

    def dispatcher(callback):
        thread = threading.Thread(target=callback, name="loop")
        thread.start()
        thread.join()

    api._keymap.set_main_thread_dispatcher(dispatcher)
    result = {}

    def worker():
        window = api.window(app="TestApp")
        node = window.find(identifier="save")
        ran_on.append(threading.current_thread().name)
        result["node"] = node

    thread = threading.Thread(target=worker, name="worker")
    thread.start()
    thread.join(timeout=5)

    assert result["node"].name == "Save"
    assert ran_on == ["worker"], "the action's own code stays on the worker"


def test_wait_refuses_to_block_the_loop_thread(ui):
    api, _element = ui
    api._keymap.set_main_thread_dispatcher(lambda callback: None)
    with pytest.raises(RuntimeError, match="event-loop thread"):
        api.wait(lambda: False, timeout=0.2)


# -- the one platform-specific call -----------------------------------------

def test_enable_content_access_is_safe_where_it_does_not_apply(ui):
    """Windows needs no equivalent, so an action calls it unconditionally.

    This used to pass for the wrong reason - there was no focused element, so
    the call returned before reaching anything.  It now walks to the window
    and finds no setter there, which is what the assertion was always meant
    to be about.
    """
    api, _element = ui
    assert api.enable_content_access() is False     # fake has no such setter


def test_enable_content_access_reaches_an_application_with_no_focus(ui):
    """The chicken and egg this exists to break.

    An application that exposes no accessibility tree has no focused element
    either - which is exactly the application the call is for - so asking
    through focus alone could never reach the ones that need it most. Steam's
    client reported no focused element and no panes, and there was no way to
    ask it for any.
    """
    api, element = ui
    asked = []

    class AppElement(FakeElement):
        def set_manual_accessibility(self, enable=True):
            asked.append(enable)

    application = AppElement("Application", key="app")
    element.parent = lambda: application
    application.parent = lambda: None
    api._keymap._focus_provider.get_focused_element = lambda: None

    assert api.focused() is None                 # nothing to ask through
    assert api.enable_content_access() is True   # ... but the window is there
    assert asked == [True]


def test_enable_content_access_reaches_the_application(ui):
    api, element = ui
    asked = []

    class AppElement(FakeElement):
        def set_manual_accessibility(self, enable=True):
            asked.append(enable)

    application = AppElement("Application", key="app")
    element.parent = lambda: application
    application.parent = lambda: None
    assert api.enable_content_access(api.node(element)) is True
    assert asked == [True]
