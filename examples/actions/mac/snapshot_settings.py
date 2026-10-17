"""Walk every tab of a settings window and dump the field values to JSON.

macOS, Terminal > Settings. Written against a screen nobody had inspected
first, to find out what the authoring skill failed to warn about. It found
four things, all now in the skill's quirks:

  - **A tab is defined by its parent, not by its role.** Tabs are
    AXRadioButtons in an AXTabGroup - and so are the ordinary radio buttons
    inside the panels. Enumerate the tab group's own children.
  - **AppKit identifiers are serial numbers.** `_NS:746` is a nib ordinal,
    not a name; address native controls by name and structure instead.
  - **A native field's label is its sibling.** The Columns field has no name
    at all; "Columns:" is a separate StaticText beside it. Geometry pairs them.
  - **Exclude the navigation from what you record**, or the tab bar's own
    selection state lands in every panel's snapshot.

Read-only. If the settings window is not open it is opened from the menu bar
and closed again afterwards; if it was already open, it is left open, on the
tab it was found on.

    python tools/run_action_file.py examples/actions/mac/snapshot_settings.py
        [output_path=~/Desktop/terminal-settings.json]
"""

import json
import pathlib

from keyhac import ThreadedAction, getLogger

logger = getLogger("SnapshotSettings")

#: Controls whose value is worth recording, as a role pattern.
VALUE_ROLES = "CheckBox|RadioButton|TextField|PopUpButton|Slider"

#: How far left of a field its label may sit, and how far its centre line may
#: differ, for the two to be considered a pair. Generous horizontally (a label
#: column can be wide) and tight vertically (rows are ~20pt apart).
LABEL_MAX_GAP = 260
LABEL_MAX_ROW_OFFSET = 8


def subrole(node) -> str | None:
    """AXSubrole, which UINode does not project - read from the element."""
    return node.element.get_attribute_value("AXSubrole")


class SnapshotSettings(ThreadedAction):
    """Record every tab's field values as JSON."""

    def __init__(self, app_name="Terminal",
                 output_path="~/Desktop/terminal-settings.json"):
        self.app_name = app_name
        self.output_path = pathlib.Path(output_path).expanduser()

    def starting(self):
        logger.info(f"snapshotting {self.app_name} settings")

    def run(self):
        ui = self.ui
        self.TAB_GROUP = ui.Locator(role="TabGroup", max_depth=4)
        self.CLOSE = ui.Locator(role="Button",
                                predicate=lambda n: subrole(n) == "AXCloseButton",
                                max_depth=1)

        window = self._settings_window()
        opened_here = window is None
        if opened_here:
            ui.activate(app=self.app_name)
            ui.menu(self.app_name, "Settings*|Preferences*",
                    until=self._settings_window)
            window = self._settings_window()
        ui.activate(node=window)
        self.front = ui.Front(node=window)

        tabs = self._tabs(window)
        if not tabs:
            raise RuntimeError("no tabs in this settings window")
        names = [tab.name for tab in tabs if tab.name]
        originally = next((tab.name for tab in tabs if str(tab.value) == "1"),
                          None)
        logger.info(f"{len(names)} tabs, currently on {originally!r}")

        snapshot = {}
        for name in names:
            self._select(window, name)
            snapshot[name] = self._read_tab(window, chrome=set(names))
            logger.info(f"  {name}: {len(snapshot[name])} values")

        if opened_here:
            # Ours to close, and identified as ours: this very window node.
            ui.click(self.CLOSE, within=window, until=ui.Gone(window))
        elif originally:
            self._select(window, originally)     # leave it as it was found

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
        proceeds. A dialog-subrole window holding a tab group is what actually
        identifies it.
        """
        for candidate in self.ui.windows(app=self.app_name):
            if (subrole(candidate) == "AXDialog"
                    and candidate.find(**self.TAB_GROUP.criteria())):
                return candidate
        return None

    def _tabs(self, window) -> list:
        """The tab buttons: the tab group's own children - not "every radio
        button near the top", which collected a panel's scrollback option as
        a seventh tab and then failed trying to select it."""
        group = window.find(**self.TAB_GROUP.criteria())
        return [child for child in group.children
                if child.role == "AXRadioButton"] if group else []

    def _select(self, window, name: str) -> None:
        tab = next((t for t in self._tabs(window) if t.name == name), None)
        if tab is None:
            raise RuntimeError(f"no tab named {name!r} any more")
        if str(tab.value) == "1":
            return                       # already there: pressing re-selects
        # The tab's own value is the signal the control publishes; wait for
        # it rather than for the window to look busy.
        self.ui.click(node=tab, given=self.front,
                      until=self.ui.Reads(tab, value="1"))

    # -- reading -------------------------------------------------------------

    def _read_tab(self, window, chrome: set) -> dict:
        """The panel's values, without the tab buttons that switch panels -
        they have values of their own, and recording them makes a config diff
        light up whenever the window was left on a different tab."""
        tree = window.reread(max_depth=12, max_nodes=800)
        nodes = list(tree.walk())
        labels = [n for n in nodes if n.role == "AXStaticText" and n.rect]
        wanted = {f"AX{role}" for role in VALUE_ROLES.split("|")}
        values = {}
        for node in nodes:
            if node.role not in wanted or node.value is None:
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

    @staticmethod
    def _label_for(node, labels) -> str | None:
        """The static text immediately left of an unlabelled field.

        Geometry does the *association* here, not the addressing - the field
        is still found by role and read by value.
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
        return best.all_text.strip().rstrip(":") if best else None
