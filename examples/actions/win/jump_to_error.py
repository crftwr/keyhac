"""Jump to the file:line in the last error a terminal printed.

Windows, Windows Terminal. The same task as `mac/jump_to_error.py`: read the
terminal's buffer through the text layer, take the last file:line in it, and
open the editor there. The ladder - whole buffer, caret line, selection -
carries across unchanged, because UI Automation's Text pattern answers all
three.

Two things do not carry across:

  - **The buffer is found by what it can do, not by its role.** On macOS a
    terminal is an AXTextArea. Windows Terminal's buffer is a `Text`,
    Notepad's a `Document`, VS Code's an `Edit` - so the search asks for the
    element that supports the Text pattern (it lists `SelectedText` among its
    attributes), which is the question this action is really asking.
  - **The error formats are Windows ones.** Paths start with a drive letter
    and use backslashes, and MSVC writes `file(line,col):` where everything
    else writes `file:line:col`. Which formats matter is domain knowledge, so
    add the ones your tools print.

    # in Windows Terminal:
    #   powershell -NoExit -ExecutionPolicy Bypass -File examples\\actions\\fixtures\\build_failure.ps1
    python tools/run_action_file.py examples/actions/win/jump_to_error.py dry_run=true
"""

import pathlib
import re
import shutil
import subprocess

from keyhac import ThreadedAction, getLogger

logger = getLogger("JumpToError")

#: A Windows path: drive-letter, UNC or relative, backslashes or slashes, and
#: no spaces - a path with a space in it is ambiguous in a line of prose, and
#: guessing where it ends is how an action opens the wrong file.
_PATH = r"(?P<path>(?:[A-Za-z]:|\\\\|\.{1,2}[\\/])[\w\\/.+\-]*\.\w+)"

PATTERNS = (
    re.compile(_PATH + r":(?P<line>\d+)(?::(?P<column>\d+))?"),   # ruff, gcc
    re.compile(_PATH + r"\((?P<line>\d+)(?:,(?P<column>\d+))?\)"),  # MSVC
    re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)'),     # Python
)


def find_locations(text: str | None) -> list:
    """Every file:line in `text`, in the order they appear."""
    matches = [m for pattern in PATTERNS for m in pattern.finditer(text or "")]
    return sorted(matches, key=lambda m: m.start())


def has_text_pattern(node) -> bool:
    """Whether the element supports UI Automation's Text pattern - the
    capability this action needs, whatever role the terminal gives it."""
    return "SelectedText" in (node.element.get_attribute_names() or [])


class JumpToError(ThreadedAction):
    """Open the editor at the last file:line the terminal mentioned."""

    def __init__(self, app_name="WindowsTerminal", editor=("code", "-g"),
                 dry_run=False):
        self.app_name = app_name
        #: How to open the result: ("code", "-g") runs `code -g path:line`.
        self.editor = editor
        self.dry_run = dry_run

    def starting(self):
        logger.info(f"looking for a file:line in {self.app_name}")

    def run(self):
        ui = self.ui
        # The frontmost terminal window, on purpose: bound to a key pressed in
        # a terminal, that is the one the operator is looking at. With several
        # open and this run from elsewhere, it is still just the frontmost.
        window = ui.window(app=self.app_name)
        if window is None:
            raise RuntimeError(f"{self.app_name} has no window open")
        # The Text role narrows the walk; the capability is what decides.
        buffer = window.wait_for(role="Text|Document|Edit",
                                 predicate=has_text_pattern, timeout=5,
                                 message=f"a text buffer in {self.app_name}")

        for how, read in (("the last match in the buffer", buffer.read_text),
                          ("the caret line", buffer.line_at_caret),
                          ("the selection", buffer.selection)):
            matches = find_locations(read())
            if matches:
                path = pathlib.Path(matches[-1].group("path"))
                line = int(matches[-1].group("line"))
                break
        else:
            return None

        if not self.dry_run:
            # `code` is a .cmd shim on Windows, which CreateProcess will not
            # find by its bare name - resolve it through PATH first.
            program = shutil.which(self.editor[0])
            if program is None:
                raise RuntimeError(f"found {path}:{line}, but {self.editor[0]!r} "
                                   f"is not on PATH")
            subprocess.run([program, *self.editor[1:], f"{path}:{line}"],
                           check=True, capture_output=True, text=True)
        return str(path), line, how

    def finished(self, result):
        if result is None:
            logger.info("no file:line found in the terminal")
            return
        path, line, how = result
        verb = "found" if self.dry_run else "opened"
        logger.info(f"{verb} {path}:{line}  (via {how})")
