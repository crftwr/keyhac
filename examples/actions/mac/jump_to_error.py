"""Jump to the file:line in the last error a terminal printed.

macOS, Terminal. The one example that works the *text* layer rather than the
tree: a terminal is a single TextArea holding an undifferentiated blob, with
no element for "the error line", so no tree search can find it.

It is also the smallest demonstration that runtime inference is not needed
for this class of work. Asking a model "which line is the error and what file
does it name" is slower, costs tokens, is not reproducible, and is *less*
accurate than the regexes below - paths and line numbers are exactly what
regexes beat language models at.

    # in Terminal:  bash examples/actions/fixtures/build_failure.sh
    python tools/run_action_file.py examples/actions/mac/jump_to_error.py dry_run=true

THE LADDER. Three ways into the text layer, cheapest first:

  1. the whole buffer, last match - "the error is the last file:line printed",
     which is usually just true after a failed command. Costs the user nothing.
  2. the line at the caret - no selection, no pointer.
  3. the selection - the fallback, and the right answer when several
     candidates are on screen and only the human knows which one matters.
"""

import pathlib
import re
import subprocess

from keyhac import ThreadedAction, getLogger

logger = getLogger("JumpToError")

#: Where errors say they are. Two shapes, because a version that knew only the
#: first found nothing in a terminal showing an ordinary Python traceback:
#:
#:   ./src/main.py:42:7                     compilers, linters, grep, ruff
#:   File "./src/main.py", line 42          Python tracebacks
#:
#: Which formats matter is domain knowledge - add the ones your tools emit.
PATTERNS = (
    re.compile(r"(?P<path>[~./][\w./+\-]*\.\w+)"         # ./src/main.py
               r":(?P<line>\d+)(?::(?P<column>\d+))?"),
    re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)'),
)


def find_locations(text: str | None) -> list:
    """Every file:line in `text`, in the order they appear."""
    matches = [m for pattern in PATTERNS for m in pattern.finditer(text or "")]
    return sorted(matches, key=lambda m: m.start())


class JumpToError(ThreadedAction):
    """Open the editor at the last file:line the terminal mentioned."""

    def __init__(self, app_name="Terminal", editor=("code", "-g"),
                 dry_run=False):
        self.app_name = app_name
        #: How to open the result: ("code", "-g") runs `code -g path:line`.
        self.editor = editor
        self.dry_run = dry_run

    def starting(self):
        logger.info(f"looking for a file:line in {self.app_name}")

    def run(self):
        ui = self.ui
        window = ui.window(app=self.app_name)
        if window is None:
            raise RuntimeError(f"{self.app_name} has no window open")
        buffer = window.find(role="TextArea")
        if buffer is None:
            raise RuntimeError(
                f"no text area in the {self.app_name} window - this action "
                f"reads a terminal's buffer, not its controls")

        for how, read in (("the last match in the buffer", buffer.read_text),
                          ("the caret line", buffer.line_at_caret),
                          ("the selection", buffer.selection)):
            matches = find_locations(read())
            if matches:
                path = pathlib.Path(matches[-1].group("path")).expanduser()
                line = int(matches[-1].group("line"))
                break
        else:
            return None

        if not self.dry_run:
            # Here on the worker, not in finished(): finished() runs on the
            # loop thread, and a child process started there holds the keyboard
            # until it exits.
            subprocess.run([*self.editor, f"{path}:{line}"],
                           check=True, capture_output=True, text=True)
        return str(path), line, how

    def finished(self, result):
        if result is None:
            logger.info("no file:line found in the terminal")
            return
        path, line, how = result
        verb = "found" if self.dry_run else "opened"
        logger.info(f"{verb} {path}:{line}  (via {how})")
