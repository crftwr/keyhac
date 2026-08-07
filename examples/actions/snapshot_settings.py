"""Walk every tab of a settings window and dump the field values to JSON.

Eval case 8, written against a screen nobody had inspected first - the point
being to find out what the authoring skill fails to warn about (§10 step 6).
It found two things, both now in references/quirks.md:

  - **AppKit identifiers are serial numbers.** `#_NS:746` is a nib ordinal, not
    a name: it changes when the window is edited and means nothing to a reader.
    The skill says "prefer identifier"; that holds for DOM ids and
    AutomationIds and is actively wrong here.
  - **A native field's label is its sibling, not its parent.** The Columns
    field is `AXTextField = '120'` with no name at all, and the string
    "Columns:" is a separate AXStaticText beside it. Nothing in the tree links
    them, so a snapshot keyed on names loses every text field on the screen.

Then it was ported to Windows, which is why the platform tables below exist -
and what porting cost is worth stating, because "portable" is not the same as
"unchanged". The *shape* survived intact: find the window by what it contains,
enumerate the tab strip's own children, select, wait for the selection to be
reported, read, restore the original tab. Every selector and every state read
had to be rewritten, and one thing could not be written at all until the
platform layer grew a `SelectionItem` pattern - a Win32 `TabItem` supports no
press action and has no value, so neither selecting a tab nor asking which tab
is current was possible.

    # macOS: open Terminal > Settings first
    python examples/actions/snapshot_settings.py [output.json]

    # Windows: open Mouse Properties first - `control main.cpl`
    python examples/actions/snapshot_settings.py [output.json]

Read-only, and it puts the originally-selected tab back.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from _runner import run_action, top_level_windows                 # noqa: E402
from keyhac.core.action import ThreadedAction                     # noqa: E402
from keyhac.core.fill import press                                # noqa: E402
from keyhac.core.uitree import find_elements, get_ui_tree         # noqa: E402
from keyhac.core.wait import evaluate_on_main_thread, wait_for    # noqa: E402
from keyhac.core import log                                       # noqa: E402

logger = log.getLogger("SnapshotSettings")

MAC = sys.platform == "darwin"

#: What the tab strip and its buttons are called. The *idea* is portable - a
#: tab is defined by its parent, not by its role - and only the spelling is not.
TAB_STRIP_ROLE = "AXTabGroup" if MAC else "Tab"
TAB_ROLE = "AXRadioButton" if MAC else "TabItem"

#: Controls whose value is worth recording. Deliberately excludes ListItem and
#: TreeItem on Windows for the same reason macOS excludes them: a list's items
#: are its contents, not its setting - the ComboBox above them carries that.
VALUE_ROLES = ("AXCheckBox|AXRadioButton|AXTextField|AXPopUpButton|AXSlider" if MAC
               else "CheckBox|RadioButton|Edit|ComboBox|Slider")

#: Where a control keeps its state. macOS puts everything in AXValue; Windows
#: splits it three ways and a reader that knows one records a fraction of the
#: screen - 1 control on a panel that actually had 5.
STATE_ATTRIBUTES = () if MAC else ("ToggleState", "IsSelected")

#: The default target. On macOS this is an application name, because that is
#: how a window is reached there; on Windows it is part of a window title,
#: because a control-panel applet has no application to speak of.
DEFAULT_TARGET = "Terminal" if MAC else "Mouse Properties"

#: How far left of a field its label may sit, and how far its centre line may
#: differ, for the two to be considered a pair.  Generous horizontally (a
#: label column can be wide) and tight vertically (rows are ~20pt apart).
LABEL_MAX_GAP = 260
LABEL_MAX_ROW_OFFSET = 8


class SnapshotSettings(ThreadedAction):
    """Record every tab's field values as JSON."""

    def __init__(self, target=DEFAULT_TARGET, output_path="settings.json"):
        self.target = target
        self.output_path = pathlib.Path(output_path).expanduser()

    def starting(self):
        logger.info(f"snapshotting {self.target} settings")

    def run(self):
        window = wait_for(lambda: self._settings_window(), timeout=20,
                          message=f"the {self.target} settings window")
        tabs = evaluate_on_main_thread(
            lambda: [node.name for node in self._tabs(window) if node.name])
        if not tabs:
            raise RuntimeError("no tabs in this settings window")
        originally = evaluate_on_main_thread(lambda: next(
            (node.name for node in self._tabs(window) if self._is_selected(node)),
            None))
        logger.info(f"{len(tabs)} tabs, currently on {originally!r}")

        chrome = set(tabs)
        snapshot = {}
        for name in tabs:
            self._select(window, name)
            snapshot[name] = self._read_tab(window, chrome)
            logger.info(f"  {name}: {len(snapshot[name])} values")

        if originally:                      # leave the UI as it was found
            self._select(window, originally)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        return snapshot

    def finished(self, result):
        total = sum(len(values) for values in result.values())
        logger.info(f"{total} values from {len(result)} tabs -> {self.output_path}")

    # -- navigation ----------------------------------------------------------

    def _settings_window(self):
        """The settings window, found by shape rather than by title.

        On macOS its title is the *selected tab* ("Profiles"), so it changes as
        the walk proceeds and cannot be matched against anything fixed; on
        Windows the applet's title is stable but its process is a shared host,
        so the title is the only thing identifying it. Either way what settles
        it is the shape: a window with a tab strip in it.
        """
        if MAC:
            from _runner import front_window
            _window, app = front_window(self.target)
            if app is None:
                return None
            candidates = app.get_attribute_value("AXWindows") or []
            candidates = [c for c in candidates
                          if c.get_attribute_value("AXSubrole") == "AXDialog"]
        else:
            candidates = top_level_windows(self.target)

        for candidate in candidates:
            if find_elements(candidate, role=TAB_STRIP_ROLE, max_depth=4):
                return candidate
        return None

    def _select(self, window, name: str) -> None:
        tab = evaluate_on_main_thread(lambda: self._tab(window, name))
        if tab is None:
            raise RuntimeError(f"no tab named {name!r} any more")
        if evaluate_on_main_thread(lambda: self._is_selected(tab)):
            return                          # already there: pressing re-selects
        press(tab)
        # Wait for the tab to report itself selected, not for the window to
        # look busy: the selection is what the control itself publishes.
        wait_for(lambda: self._is_selected(self._tab(window, name)),
                 timeout=10, message=f"the {name!r} tab to become selected")

    @staticmethod
    def _is_selected(tab) -> bool:
        """Whether a tab is the current one.

        The one thing that did not survive the port even in principle. macOS
        publishes it as the radio button's AXValue, "1"; a Win32 TabItem has no
        value at all - `.value` is None whether it is selected or not - and
        reports through the SelectionItem pattern instead. Reading it the macOS
        way on Windows silently matches nothing, so the wait above would time
        out on a tab that had in fact been selected.
        """
        if tab is None:
            return False
        if MAC:
            return str(tab.value) == "1"
        return bool(tab.element.get_attribute_value("IsSelected"))

    @staticmethod
    def _tabs(window) -> list:
        """The tab buttons - the tab strip's own children.

        Not "every AXRadioButton near the top", which is what the first version
        asked for: the Window pane contains a scrollback AXRadioGroup whose
        buttons are radio buttons too, so the walk picked up "Limit to
        available memory" as a seventh tab and then failed trying to select it.
        A tab is defined by its parent, not by its role - and that holds on
        Windows too, where the panels are full of ordinary radio buttons.
        """
        strips = find_elements(window, role=TAB_STRIP_ROLE, max_depth=6)
        if not strips:
            return []
        return [child for child in strips[0].children if child.role == TAB_ROLE]

    @classmethod
    def _tab(cls, window, name: str):
        for node in cls._tabs(window):
            if node.name == name:
                return node
        return None

    # -- reading -------------------------------------------------------------

    def _read_tab(self, window, chrome: set) -> dict:
        """The panel's values, without the navigation that switches panels.

        `chrome` is the tab names: the tab buttons carry state of their own, so
        a naive walk records the whole tab strip's selection into every tab's
        snapshot - spurious entries per panel, and a config diff that lights up
        whenever someone left the window on a different tab. On Windows this
        matters more than it did on macOS, not less: a TabItem has no value but
        it does have IsSelected, so it reappears the moment the reader learns
        to look there.
        """
        def read():
            tree = get_ui_tree(window, max_depth=12, max_nodes=800)
            nodes = list(tree.walk())
            labels = [n for n in nodes
                      if n.role in ("AXStaticText", "Text") and n.rect]
            values = {}
            for node in nodes:
                if node.role == TAB_ROLE:
                    continue
                if not any(node.role == role for role in VALUE_ROLES.split("|")):
                    continue
                state = self._state_of(node)
                if state is None:
                    continue
                key = node.name or self._label_for(node, labels)
                if not key or key in chrome:
                    continue
                # Keep both when two controls share a label rather than letting
                # the second silently overwrite the first.
                if key in values and values[key] != state:
                    key = f"{key} ({node.role})"
                values[key] = state
            return values

        return evaluate_on_main_thread(read)

    @staticmethod
    def _state_of(node):
        """What this control is currently set to, wherever it keeps that.

        On macOS the answer is always AXValue. On Windows it is `value` for an
        Edit or ComboBox, `ToggleState` for a CheckBox (0/1, and 2 for
        indeterminate) and `IsSelected` for a RadioButton - three patterns, and
        no control implements more than one of them.
        """
        if node.value is not None:
            return node.value
        for attribute in STATE_ATTRIBUTES:
            state = node.element.get_attribute_value(attribute)
            if state is not None:
                return state
        return None

    @staticmethod
    def _label_for(node, labels) -> str | None:
        """The static text sitting immediately left of an unlabelled field.

        Geometry is doing the *association* here, not the addressing - the
        field is still found by role and read by value. Native text fields
        carry no name at all, so without this every one of them is dropped.
        """
        x, y, _w, h = node.rect
        middle = y + h / 2
        best, best_gap = None, LABEL_MAX_GAP
        for label in labels:
            lx, ly, lw, lh = label.rect
            gap = x - (lx + lw)
            if gap < 0 or gap > best_gap:
                continue
            if abs((ly + lh / 2) - middle) > LABEL_MAX_ROW_OFFSET:
                continue
            best, best_gap = label, gap
        return (best.all_text.strip().rstrip(":") if best else None)


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else "settings.json"
    sys.exit(run_action(SnapshotSettings(output_path=output)))
