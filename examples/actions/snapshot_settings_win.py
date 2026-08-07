"""Walk every tab of a settings dialog and dump the field values to JSON.

The Windows half of `snapshot_settings.py`, written against Mouse Properties -
a classic Win32 property sheet, present on every install, whose five tabs
between them use every way Windows has of holding a value.

Two files rather than one with branches in it, because an action is generated
for one screen and gains nothing from carrying selectors for a tree it will
never meet. What the pair is for is the comparison: the *shape* is the same
line for line - find the window by what it contains, enumerate the tab strip's
own children, select, wait for the selection to be reported, read, restore the
original tab - and not one selector is shared. `README.md` records what the
port measured.

Three things here have no counterpart in the macOS file:

  - **A tab is reached through SelectionItem or not at all.** A Win32 `TabItem`
    supports no Invoke, no Toggle and no Expand - `get_action_names()` returns
    `[]` - so `press()` reaching `Select` is the only way to switch tabs, and
    `IsSelected` the only way to ask which one is current. `.value` is None
    whether a tab is selected or not, so the macOS test for it matches nothing.
  - **State lives in three places.** `value` for an Edit or ComboBox,
    `ToggleState` for a CheckBox, `IsSelected` for a RadioButton. No control
    implements more than one, and reading only the first two finds a fraction
    of the panel.
  - **Excluding the navigation matters more, not less.** A TabItem has no value
    but it does have `IsSelected`, so the tab strip walks straight back into the
    output the moment the reader learns where Windows keeps state.

Read-only: it switches tabs and puts the original one back, and never presses
OK or Apply. Close the dialog yourself when it is done.

    # open Mouse Properties first:  control main.cpl
    python examples/actions/snapshot_settings_win.py [output.json]
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

logger = log.getLogger("SnapshotWinSettings")

#: Controls whose value is worth recording.  ListItem and TreeItem are left out
#: on purpose: a list's items are its contents, not its setting - the ComboBox
#: above them carries that, and recording all seventeen cursor schemes would
#: bury the one that is selected.
VALUE_ROLES = "CheckBox|RadioButton|Edit|ComboBox|Slider"

#: Where a control keeps its state, in the order worth asking.  `value` first
#: because an Edit answers it directly; the other two are patterns that only
#: one kind of control implements each.
STATE_ATTRIBUTES = ("ToggleState", "IsSelected")

#: How far left of a field its label may sit, and how far its centre line may
#: differ, for the two to be considered a pair.  Generous horizontally (a label
#: column can be wide) and tight vertically (rows are ~20px apart).  Carried
#: over from the macOS file unchanged, which is the one technique that turned
#: out not to be platform-specific at all.
LABEL_MAX_GAP = 260
LABEL_MAX_ROW_OFFSET = 8


class SnapshotWinSettings(ThreadedAction):
    """Record every tab's field values as JSON."""

    def __init__(self, title="Mouse Properties", output_path="settings.json"):
        self.title = title
        self.output_path = pathlib.Path(output_path).expanduser()

    def starting(self):
        logger.info(f"snapshotting {self.title}")

    def run(self):
        window = wait_for(lambda: self._settings_window(), timeout=20,
                          message=f"the {self.title} dialog")
        tabs = evaluate_on_main_thread(
            lambda: [node.name for node in self._tabs(window) if node.name])
        if not tabs:
            raise RuntimeError("no tab strip in this dialog")
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
        """The dialog, confirmed by shape rather than taken on its title alone.

        A control-panel applet has no application of its own to search from -
        it is hosted in a shared process - so the title is what finds the
        candidates. It is not what identifies them: a window with a tab strip
        in it is, and requiring that is what stops the walk starting on some
        other window that happens to be named similarly.
        """
        for candidate in top_level_windows(self.title):
            if find_elements(candidate, role="Tab", max_depth=4):
                return candidate
        return None

    def _select(self, window, name: str) -> None:
        tab = evaluate_on_main_thread(lambda: self._tab(window, name))
        if tab is None:
            raise RuntimeError(f"no tab named {name!r} any more")
        if evaluate_on_main_thread(lambda: self._is_selected(tab)):
            return                          # already there: pressing re-selects
        press(tab)                          # reaches SelectionItem::Select
        # Wait for the tab to report itself selected, not for the dialog to look
        # busy: the selection is what the control itself publishes.
        wait_for(lambda: self._is_selected(self._tab(window, name)),
                 timeout=10, message=f"the {name!r} tab to become selected")

    @staticmethod
    def _is_selected(tab) -> bool:
        """Whether a tab is the current one - the SelectionItem pattern.

        Not `.value`, which is None for a TabItem however the tab is set. The
        macOS file asks `str(tab.value) == "1"`, and that test run here matches
        nothing at all: the wait above would then time out on a tab that had in
        fact been selected.
        """
        return tab is not None and bool(
            tab.element.get_attribute_value("IsSelected"))

    @staticmethod
    def _tabs(window) -> list:
        """The tab buttons - the tab strip's own children.

        By parent rather than by role, and the panels here are the reason: the
        Wheel tab holds two ordinary RadioButtons, and anything looking for
        "the selectable things near the top" would collect them and then fail
        trying to select one as a tab.
        """
        strips = find_elements(window, role="Tab", max_depth=6)
        if not strips:
            return []
        return [child for child in strips[0].children if child.role == "TabItem"]

    @classmethod
    def _tab(cls, window, name: str):
        for node in cls._tabs(window):
            if node.name == name:
                return node
        return None

    # -- reading -------------------------------------------------------------

    def _read_tab(self, window, chrome: set) -> dict:
        """The panel's values, without the navigation that switches panels.

        `chrome` is the tab names. A TabItem carries no value, so a reader that
        only knew about values would never have recorded one - but it does
        carry IsSelected, so the whole tab strip's selection state lands in
        every panel's snapshot the moment the reader is taught to look there,
        and a config diff then lights up whenever the dialog was left on a
        different tab.
        """
        def read():
            tree = get_ui_tree(window, max_depth=12, max_nodes=800)
            nodes = list(tree.walk())
            labels = [n for n in nodes if n.role == "Text" and n.rect]
            values = {}
            for node in nodes:
                if node.role == "TabItem":
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
        """What this control is set to, from whichever pattern holds it."""
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
        field is still found by role and read by state. It is what recovers the
        Hardware tab, whose three Edits have no names at all.
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
    sys.exit(run_action(SnapshotWinSettings(output_path=output)))
