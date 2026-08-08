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

Read-only, and it puts the originally-selected tab back.

**macOS.** `snapshot_settings_win.py` is the same task written against Windows,
and the pair is deliberately two files rather than one with branches in it: an
action is generated for one screen, and nothing is gained by making it carry
selectors for a tree it will never meet. Reading them side by side is also the
clearest statement of what a change of platform costs - the shape is the same
line for line, and not one selector survives. `examples/actions/README.md`
records what that measured.

    # open Terminal > Settings first
    python examples/actions/snapshot_settings.py [output.json]
"""

import json
import pathlib
import sys

_ACTIONS = pathlib.Path(__file__).resolve().parents[1]   # examples/actions
sys.path.insert(0, str(_ACTIONS.parents[1]))             # the repo root
sys.path.insert(0, str(_ACTIONS))                        # _runner.py, fixtures/

from _runner import run_action                                    # noqa: E402
from keyhac.core.action import ThreadedAction                     # noqa: E402
from keyhac.core import log                                       # noqa: E402

logger = log.getLogger("SnapshotSettings")

#: Controls whose value is worth recording.
VALUE_ROLES = "AXCheckBox|AXRadioButton|AXTextField|AXPopUpButton|AXSlider"

#: How far left of a field its label may sit, and how far its centre line may
#: differ, for the two to be considered a pair.  Generous horizontally (a
#: label column can be wide) and tight vertically (rows are ~20pt apart).
LABEL_MAX_GAP = 260
LABEL_MAX_ROW_OFFSET = 8


class SnapshotSettings(ThreadedAction):
    """Record every tab's field values as JSON."""

    def __init__(self, app_name="Terminal", output_path="~/Desktop/settings.json"):
        self.app_name = app_name
        self.output_path = pathlib.Path(output_path).expanduser()

    def starting(self):
        logger.info(f"snapshotting {self.app_name} settings")

    def run(self):
        window = self.ui.wait(self._settings_window, timeout=20,
                              message=f"the {self.app_name} settings window")
        tabs = [(node.name, node.value)
                for node in self._tabs(window) if node.name]
        if not tabs:
            raise RuntimeError("no tabs in this settings window")
        originally = next((name for name, value in tabs if str(value) == "1"), None)
        logger.info(f"{len(tabs)} tabs, currently on {originally!r}")

        chrome = {name for name, _ in tabs}
        snapshot = {}
        for name, _value in tabs:
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

        Its title is the *selected tab* ("Profiles"), so it changes as the walk
        proceeds and cannot be matched against anything fixed.  AXDialog plus a
        tab group is what actually identifies it.
        """
        for candidate in self.ui.windows(app=self.app_name):
            subrole = self.ui.on_main_thread(
                lambda c=candidate: c.element.get_attribute_value("AXSubrole"))
            if subrole != "AXDialog":
                continue
            if candidate.find_all(role="AXTabGroup", max_depth=4):
                return candidate
        return None

    def _select(self, window, name: str) -> None:
        tab = self._tab(window, name)
        if tab is None:
            raise RuntimeError(f"no tab named {name!r} any more")
        if str(tab.value) == "1":
            return                          # already there: pressing re-selects
        tab.press()
        # Wait for the tab to report itself selected, not for the window to
        # look busy: the value is the signal the control itself publishes.
        self.ui.wait(
            lambda: str(getattr(self._tab(window, name), "value", "")) == "1",
            timeout=10, message=f"the {name!r} tab to become selected")

    @staticmethod
    def _tabs(window) -> list:
        """The tab buttons - the tab group's own children.

        Not "every AXRadioButton near the top", which is what the first version
        asked for: the Window pane contains a scrollback AXRadioGroup whose
        buttons are radio buttons too, so the walk picked up "Limit to
        available memory" as a seventh tab and then failed trying to select it.
        A tab is defined by its parent, not by its role.
        """
        groups = window.find_all(role="AXTabGroup", max_depth=6)
        if not groups:
            return []
        return [child for child in groups[0].children
                if child.role == "AXRadioButton"]

    @classmethod
    def _tab(cls, window, name: str):
        for node in cls._tabs(window):
            if node.name == name:
                return node
        return None

    # -- reading -------------------------------------------------------------

    def _read_tab(self, window, chrome: set) -> dict:
        """The panel's values, without the navigation that switches panels.

        `chrome` is the tab names: the tab buttons are AXRadioButtons with
        values of their own, so a naive walk records the whole tab bar's
        selection state into every tab's snapshot - six spurious entries per
        panel, and a config diff that lights up whenever someone left the
        window on a different tab.
        """
        def read():
            tree = window.reread(max_depth=12, max_nodes=800)
            nodes = list(tree.walk())
            labels = [n for n in nodes if n.role == "AXStaticText" and n.rect]
            values = {}
            for node in nodes:
                if not any(node.role == role for role in VALUE_ROLES.split("|")):
                    continue
                if node.value is None:
                    continue
                key = node.name or self._label_for(node, labels)
                if not key or key in chrome:
                    continue
                # Keep both when two controls share a label rather than letting
                # the second silently overwrite the first.
                if key in values and values[key] != node.value:
                    key = f"{key} ({node.role})"
                values[key] = node.value
            return values

        return self.ui.on_main_thread(read)

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
    output = sys.argv[1] if len(sys.argv) > 1 else "~/Desktop/settings.json"
    sys.exit(run_action(SnapshotSettings(output_path=output)))
