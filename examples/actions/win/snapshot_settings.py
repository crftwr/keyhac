"""Walk every tab of a settings dialog and dump the field values to JSON.

Windows, Mouse Properties - a classic Win32 property sheet, present on every
install, whose five tabs between them use every way Windows has of holding a
value. The same task as `mac/snapshot_settings.py`, and the shape is the same
line for line: find the window by what it contains, enumerate the tab strip's
own children, select each tab and wait for it to report itself selected, read
the panel, put things back. Not one selector is shared.

Three things here have no counterpart in the macOS file:

  - **A tab is reached through SelectionItem or not at all.** A Win32 TabItem
    supports no Invoke, no Toggle and no Expand, and its `.value` is None
    whether it is selected or not. `ui.click()` selects it, and `IsSelected`
    is the only way to ask which tab is current - so the click's `until=` is
    a callable reading it, where the macOS file can use `Reads(value="1")`.
  - **State lives in three places.** `value` for an Edit or ComboBox,
    `ToggleState` for a CheckBox, `IsSelected` for a RadioButton. No control
    implements more than one, and reading only the first two finds a fraction
    of the panel.
  - **Excluding the navigation matters more, not less.** A TabItem has no
    value but does have `IsSelected`, so the tab strip walks straight back
    into the output the moment the reader learns where Windows keeps state.

Read-only. If the dialog is not open it is opened with `control main.cpl` and
cancelled afterwards - Cancel, because nothing was changed and nothing should
be applied. If it was already open it is left open, on the tab it was found
on, and OK and Apply are never pressed.

    python tools/run_action_file.py examples/actions/win/snapshot_settings.py
        [output_path=~/Desktop/mouse-settings.json]
"""

import json
import pathlib
import subprocess

from keyhac import ThreadedAction, getLogger

logger = getLogger("SnapshotSettings")

#: Controls whose value is worth recording. ListItem and TreeItem are left out
#: on purpose: a list's items are its contents, not its setting - the ComboBox
#: above them carries that.
VALUE_ROLES = ("CheckBox", "RadioButton", "Edit", "ComboBox", "Slider")

#: Where a control keeps its state when `value` is None - the two patterns,
#: each implemented by one kind of control.
STATE_ATTRIBUTES = ("ToggleState", "IsSelected")

#: Label pairing, carried over from the macOS file unchanged: the one
#: technique that turned out not to be platform-specific at all.
LABEL_MAX_GAP = 260
LABEL_MAX_ROW_OFFSET = 8


def is_selected(tab) -> bool:
    """Whether a tab is the current one - the SelectionItem pattern, read
    live from the element rather than from the snapshot."""
    return tab is not None and bool(tab.element.get_attribute_value("IsSelected"))


class SnapshotSettings(ThreadedAction):
    """Record every tab's field values as JSON."""

    def __init__(self, title="Mouse Properties", applet="main.cpl",
                 output_path="~/Desktop/mouse-settings.json"):
        self.title = title
        self.applet = applet
        self.output_path = pathlib.Path(output_path).expanduser()

    def starting(self):
        logger.info(f"snapshotting {self.title}")

    def run(self):
        ui = self.ui
        self.TAB_STRIP = ui.Locator(role="Tab", max_depth=4)
        self.CANCEL = ui.Locator(role="Button", name="Cancel", max_depth=3)

        window = self._settings_window()
        opened_here = window is None
        if opened_here:
            # No check=True: control.exe exits 1 even when the dialog opens.
            # The wait for the window below is the real check.
            subprocess.run(["control", self.applet],
                           capture_output=True, text=True)
            window = ui.wait(self._settings_window, timeout=20,
                             message=f"the {self.title} dialog to open")
        ui.activate(node=window)
        self.front = ui.Front(node=window)

        tabs = self._tabs(window)
        if not tabs:
            raise RuntimeError("no tab strip in this dialog")
        names = [tab.name for tab in tabs if tab.name]
        originally = next((tab.name for tab in tabs if is_selected(tab)), None)
        logger.info(f"{len(names)} tabs, currently on {originally!r}")

        snapshot = {}
        for name in names:
            self._select(window, name)
            snapshot[name] = self._read_tab(window, chrome=set(names))
            logger.info(f"  {name}: {len(snapshot[name])} values")

        # Written before the cleanup, so a close that fails cannot throw away
        # a read that succeeded - which is exactly what the first Windows run
        # did.
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

        if opened_here:
            # Ours to close, identified as ours: the dialog this run opened.
            # The front is taken again first rather than required: something
            # took it during the walk on the first Windows run, and Cancel on a
            # dialog this run opened is safe to aim at wherever it sits.
            ui.activate(node=window)
            ui.click(self.CANCEL, within=window, until=ui.Gone(window))
        elif originally:
            self._select(window, originally)     # leave it as it was found
        return snapshot

    def finished(self, result):
        total = sum(len(values) for values in result.values())
        logger.info(f"{total} values from {len(result)} tabs -> {self.output_path}")

    # -- navigation ----------------------------------------------------------

    def _settings_window(self):
        """The dialog, confirmed by shape rather than taken on its title alone.

        A control-panel applet has no application of its own to search from -
        it is hosted in a shared process - so the title finds the candidates
        and a tab strip inside is what identifies the right one.
        """
        for candidate in self.ui.windows(title=self.title):
            if candidate.find(**self.TAB_STRIP.criteria()):
                return candidate
        return None

    def _tabs(self, window) -> list:
        """The tab buttons: the tab strip's own children. By parent rather
        than by role - the Wheel tab holds ordinary RadioButtons, which a
        search for "the selectable things near the top" would collect."""
        strip = window.find(**self.TAB_STRIP.criteria())
        return [child for child in strip.children
                if child.role == "TabItem"] if strip else []

    def _select(self, window, name: str) -> None:
        tab = next((t for t in self._tabs(window) if t.name == name), None)
        if tab is None:
            raise RuntimeError(f"no tab named {name!r} any more")
        if is_selected(tab):
            return                       # already there: pressing re-selects
        self.ui.click(node=tab, given=self.front,
                      until=lambda: is_selected(tab))

    # -- reading -------------------------------------------------------------

    def _read_tab(self, window, chrome: set) -> dict:
        """The panel's values, without the tab strip that switches panels."""
        tree = window.reread(max_depth=12, max_nodes=800)
        nodes = list(tree.walk())
        labels = [n for n in nodes if n.role == "Text" and n.rect]
        values = {}
        for node in nodes:
            if node.role not in VALUE_ROLES:
                continue
            state = self._state_of(node)
            if state is None:
                continue
            key = node.name or self._label_for(node, labels)
            if not key or key in chrome:
                continue
            if key in values and values[key] != state:
                key = f"{key} ({node.role})"
            values[key] = state
        return values

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
        """The static text immediately left of an unlabelled field - what
        recovers the Hardware tab, whose three Edits have no names at all."""
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
