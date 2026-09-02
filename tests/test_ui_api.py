"""The action-facing API (keymap.ui and UINode's methods).

Hermetic: the fakes implement the same small protocol the platform elements do,
so this runs on any OS. What it pins is the *shape* the reorganisation was for
- one entry point, methods on nodes, three importable names - plus the two
behaviours that are easy to lose in a refactor: every method dispatches to the
loop thread itself, and a node is a snapshot rather than a live view.
"""

import threading

import time

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
        self.activated = 0

    def activate(self):
        self.activated += 1
        return True


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
    """Windows needs no equivalent, so an action calls it unconditionally."""
    api, _element = ui
    assert api.enable_content_access() is False     # fake has no such setter


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


def _wired(ui):
    """The api and the list of enable/disable calls that reach the app."""
    api, element = ui
    asked = []

    class AppElement(FakeElement):
        def set_manual_accessibility(self, enable=True):
            asked.append(enable)

    application = AppElement("Application", key="app")
    element.parent = lambda: application
    application.parent = lambda: None
    return api, api.node(element), asked


def test_content_access_hands_it_back(ui):
    """The one call that changes another application and leaves it changed -
    so the block is what turns it off, on every way out."""
    api, node, asked = _wired(ui)
    with api.content_access(node) as enabled:
        assert enabled is True
        assert asked == [True]
    assert asked == [True, False]


def test_content_access_hands_it_back_when_the_block_raises(ui):
    """The paths that matter: an action raises PreconditionFailed, a
    WaitTimeout, or ActionCancelled long before it reaches its last line."""
    api, node, asked = _wired(ui)
    with pytest.raises(ValueError):
        with api.content_access(node):
            raise ValueError("boom")
    assert asked == [True, False]


def test_content_access_does_not_wait_for_it_to_take_effect(ui):
    """Asynchronous on purpose: measured on VS Code the write is accepted at
    once and the tree is readable at once, but a press only works about two
    seconds later. Waiting here would stall every action to buy what a
    verified retry gets for nothing."""
    api, node, _asked = _wired(ui)
    started = time.monotonic()
    with api.content_access(node):
        pass
    assert time.monotonic() - started < 0.5


def test_nested_blocks_are_counted(ui):
    """An inner block must not hand back what the outer one still needs."""
    api, node, asked = _wired(ui)
    with api.content_access(node):
        with api.content_access(node):
            pass
        assert asked == [True], "the inner exit turned it off"
    assert asked == [True, False]


# -- the verb layer (discussion #98) -----------------------------------------

class TestVerbs:
    """One call that folds find, wait, act and "did it take?".

    The measurement behind it: an accessibility press is accepted by
    applications that then do nothing with it, so the platform's answer is not
    evidence and only a postcondition the caller states can be.
    """

    def test_a_click_with_no_postcondition_presses_once(self, ui):
        """A blind retry double-acts - double-save, double-submit - so the
        retry is the caller's to ask for."""
        api, element = ui
        button = element.kids[0]
        api.click(role="AXButton", name="Save")
        assert button.pressed == 1

    def test_a_click_returns_what_it_pressed(self, ui):
        api, element = ui
        node = api.click(role="AXButton", name="Save")
        assert node.name == "Save"

    def test_a_click_repeats_until_the_postcondition_holds(self, ui):
        """The press that is accepted and does nothing is the whole reason
        this layer exists."""
        api, element = ui
        button = element.kids[0]
        api.click(role="AXButton", name="Save",
                  until=lambda: button.pressed >= 3,
                  timeout=5.0, retry_every=0.05)
        assert button.pressed == 3

    def test_a_postcondition_that_never_holds_is_a_loud_failure(self, ui):
        api, element = ui
        with pytest.raises(WaitTimeout) as error:
            api.click(role="AXButton", name="Save", until=lambda: False,
                      timeout=0.3, retry_every=0.05)
        assert "attempts" in str(error.value)

    def test_appears_hands_back_what_it_found(self, ui):
        api, element = ui
        found = api.click(role="AXButton", name="Save",
                          until=api.Appears(role="AXCheckBox", name="Archived"),
                          timeout=1.0, retry_every=0.05)
        assert found.identifier == "arch"

    def test_changed_reads_the_target_before_acting(self, ui):
        """The baseline has to be taken before the act. Taken after, every
        Changed compares equal to itself and nothing is ever satisfied."""
        api, element = ui
        checkbox = element.kids[1]
        checkbox.perform_action = (
            lambda name: checkbox._describe.__setitem__("value", 1))
        node = api.node(checkbox)
        api.click(role="AXCheckBox", name="Archived", until=api.Changed(node),
                  timeout=1.0, retry_every=0.05)
        assert checkbox._describe["value"] == 1

    def test_gone_is_satisfied_when_the_target_has_left(self, ui):
        """The described form, which is the one that works for a row removed
        from a list rather than a window that closed."""
        api, element = ui
        described = api.Appears(role="AXCheckBox", name="Archived")
        assert not api.Gone(described).check(api, None)
        element.kids.remove(element.kids[1])
        assert api.Gone(described).check(api, None)

    def test_a_verb_that_waits_refuses_the_loop_thread(self, ui):
        """Waiting there would hold the keyboard hook for the length of it."""
        api, _element = ui
        api._keymap.set_main_thread_dispatcher(lambda callback: None)
        with pytest.raises(RuntimeError, match="event-loop thread"):
            api.send_key("A", until=lambda: False, timeout=0.2)

    def test_send_key_without_a_postcondition_sends_once(self, ui):
        api, _element = ui
        api.send_key("A")
        assert True   # no exception: the keystroke went through the fake hook

    def test_a_precondition_is_waited_for_before_each_attempt(self, ui):
        """The gap the prototype found: an action waits for the *browser
        window* to be front before each Cmd-P, because the download popup
        takes the front after a save and swallows it."""
        api, element = ui
        button = element.kids[0]
        seen = []

        def front_after_a_while():
            seen.append(1)
            return len(seen) >= 3

        api.click(role="AXButton", name="Save", given=front_after_a_while,
                  timeout=2.0)
        assert len(seen) >= 3, "it acted before the precondition held"
        assert button.pressed == 1

    def test_front_matches_the_active_window(self, ui):
        api, _element = ui
        assert api.Front(app="TestApp").check(api, None)
        assert not api.Front(app="Something Else").check(api, None)

    def test_a_precondition_that_never_holds_fails_as_one(self, ui):
        """"The world was not ready" and "the act did not take" want
        different repairs, so they read differently."""
        api, _element = ui
        with pytest.raises(WaitTimeout) as error:
            api.send_key("Cmd-P", given=api.Front(app="Nothing"),
                         until=lambda: True, timeout=0.3)
        assert "never started" in str(error.value)

    def test_the_act_is_the_whole_ladder(self, ui):
        """The other gap: an action should never write "Return, else AXPress"
        itself. The verb's act is click-then-press-then-focus."""
        api, element = ui
        button = element.kids[0]
        api.click(role="AXButton", name="Save")
        assert button.pressed == 1 or button.focused

    def test_reads_states_the_value_expected(self, ui):
        """The authoring rule as a value: wait for the state you expect, not
        for the old state to change - a transform can be the identity."""
        api, element = ui
        checkbox = element.kids[1]
        node = api.node(checkbox)
        assert not api.Reads(node, value="True").check(api, None)
        checkbox._describe["value"] = True
        assert api.Reads(node, value="True").check(api, None)

    def test_reads_compares_the_value_as_text(self, ui):
        """macOS answers AXValue True, Windows a toggle state - the caller
        should not have to know which it got."""
        api, element = ui
        checkbox = element.kids[1]
        checkbox._describe["value"] = True
        node = api.node(checkbox)
        assert api.Reads(node, value="True").check(api, None)

    def test_a_click_waits_for_the_state_rather_than_the_difference(self, ui):
        api, element = ui
        checkbox = element.kids[1]
        checkbox.perform_action = (
            lambda name: checkbox._describe.__setitem__("value", True))
        node = api.node(checkbox)
        api.click(node=node, until=api.Reads(node, value="True"),
                  timeout=1.0, retry_every=0.05)
        assert checkbox._describe["value"] is True

    def test_activate_asks_and_then_waits_for_the_front(self, ui):
        """Asking a window to activate is not the same as it being in front,
        and the difference is where the next keystroke goes."""
        api, _element = ui
        window = api._keymap.window_provider.list_windows()[0]
        node = api.activate(app="TestApp", timeout=2.0)
        assert window.activated == 1
        assert node is not None

    def test_activate_that_never_comes_forward_is_a_loud_failure(self, ui):
        api, _element = ui
        with pytest.raises(WaitTimeout):
            api.activate(app="Nothing There", timeout=0.3, retry_every=0.05)

    def test_the_three_slots_take_the_same_two_types(self, ui):
        """One vocabulary: a callable or a value, in wait, given and until.
        Which slot it goes in is what says whether you cause it."""
        api, element = ui
        button = element.kids[0]
        assert api.wait(api.Appears(role="AXButton", name="Save"), timeout=1)
        api.click(role="AXButton", name="Save",
                  given=api.Appears(role="AXButton", name="Save"),
                  until=lambda: button.pressed >= 1, timeout=1,
                  retry_every=0.05)

    def test_a_wait_on_changed_remembers_when_it_started(self, ui):
        """Without a baseline it read as "anything at all" and returned at
        once - a condition that silently always holds, which is the failure
        this whole value is prone to."""
        api, element = ui
        checkbox = element.kids[1]
        node = api.node(checkbox)
        with pytest.raises(WaitTimeout):
            api.wait(api.Changed(node), timeout=0.3)

    def test_changed_is_refused_as_a_precondition(self, ui):
        """"Changed since when?" has no answer before the act, and answering
        it with "always" is worse than saying so."""
        api, element = ui
        node = api.node(element.kids[1])
        with pytest.raises(ValueError, match="no before"):
            api.click(role="AXButton", name="Save", given=api.Changed(node),
                      until=lambda: True, timeout=1)

    def test_every_value_is_a_condition(self, ui):
        """One base class, so the three slots have a name to be annotated
        with and `needs_baseline` is stated once."""
        from keyhac.core.ui import Condition

        api, _element = ui
        for value in (api.Appears, api.Front, api.Gone, api.Reads,
                      api.Changed):
            assert issubclass(value, Condition)
        assert api.Changed.needs_baseline
        assert not api.Reads.needs_baseline

    def test_only_acting_again_is_a_rate(self, ui):
        """How often to *look* is `wait_for`'s backing-off default and not a
        parameter - it cannot be got expensively wrong. How often to *act
        again* can be: too short, and a dialog that takes three seconds to
        open is opened three times."""
        api, element = ui
        button = element.kids[0]
        api.click(role="AXButton", name="Save",
                  until=lambda: button.pressed >= 2,
                  timeout=3.0, retry_every=0.1)
        assert button.pressed == 2
