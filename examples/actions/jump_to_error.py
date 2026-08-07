"""Jump to the file:line in the last error a terminal printed.

The fourth hand-written action of doc/dev/ai-integration.md §10, and the one
that exercises the *text* layer rather than the tree.  A terminal is a single
AXTextArea holding an undifferentiated blob: there is no element for "the error
line", so no amount of tree searching finds it (§6).

It is also the smallest possible demonstration that runtime inference is not
needed for this class of work.  Asking a model "which line is the error and
what file does it name" is slower, costs tokens, is not reproducible, and is
*less* accurate than the regex below - paths, line numbers and URLs are exactly
what regexes beat language models at.

Run it (macOS, with a Terminal window open that has printed an error):

    python examples/actions/jump_to_error.py

THE LADDER (§6).  Three ways into the text layer, cheapest first, and this
action tries them in that order:

  1. whole value + positional logic - "the error is the last matching line",
     which is usually just true after a failed command.  Costs the user nothing.
  2. the line at the caret - one keystroke, no selection, no pointer.
  3. the selection - the fallback, and the right answer when several candidates
     are on screen and only the human knows which one matters.
"""

import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from _runner import front_window, run_action                      # noqa: E402
from keyhac.core.action import ThreadedAction                     # noqa: E402
from keyhac.core.uitree import find_element                       # noqa: E402
from keyhac.core.wait import evaluate_on_main_thread              # noqa: E402
from keyhac.core import log                                       # noqa: E402

logger = log.getLogger("JumpToError")

#: Where errors say they are.  Two shapes, because the first version of this
#: action knew only the first one and found nothing in a terminal showing a
#: perfectly ordinary Python traceback:
#:
#:   ./src/main.py:42:7                     compilers, linters, grep, ruff
#:   File "./src/main.py", line 42          Python tracebacks
#:
#: Which formats matter is domain knowledge - the thing that legitimately stays
#: in a prompt (§8.5) rather than being inferred.  Add the ones your tools emit.
PATTERNS = (
    re.compile(
        r"(?P<path>(?:[A-Za-z]:)?[~./][\w./+\-]*\.\w+)"      # ./src/main.py
        r":(?P<line>\d+)"
        r"(?::(?P<column>\d+))?"
    ),
    re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)'),
)


def find_locations(text: str):
    """Every file:line in `text`, in the order they appear."""
    matches = [m for pattern in PATTERNS for m in pattern.finditer(text or "")]
    return sorted(matches, key=lambda m: m.start())


class JumpToError(ThreadedAction):
    """Open the editor at the last file:line the terminal mentioned."""

    def __init__(self, app_name="Terminal", editor=("code", "-g"), dry_run=False):
        self.app_name = app_name
        #: How to open the result. ("code", "-g") -> `code -g path:line`.
        self.editor = editor
        self.dry_run = dry_run

    def starting(self):
        logger.info(f"looking for a file:line in {self.app_name}")

    def run(self):
        window, _app = evaluate_on_main_thread(
            lambda: front_window(self.app_name))
        if window is None:
            raise RuntimeError(f"{self.app_name} has no window open")

        text_area = evaluate_on_main_thread(
            lambda: find_element(window, role="AXTextArea"))
        if text_area is None:
            raise RuntimeError(
                f"no text area in the {self.app_name} window - this action "
                f"reads the text layer, not the control tree")

        for strategy in (self._from_whole_text, self._from_caret_line,
                         self._from_selection):
            location = strategy(text_area.element)
            if location is not None:
                return location
        return None

    def finished(self, result):
        if result is None:
            logger.info("no file:line found in the terminal")
            return
        path, line, how = result
        logger.info(f"{path}:{line}  (found via {how})")
        if self.dry_run:
            return
        command = [*self.editor, f"{path}:{line}"]
        try:
            subprocess.run(command, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            # A missing editor is a bad configuration, not a bad read: say
            # which it was rather than reporting "jump failed".
            logger.error(f"found {path}:{line} but {command[0]} failed: {error}")

    # -- the ladder ---------------------------------------------------------

    def _from_whole_text(self, element):
        """Rung 1: the last match in the whole buffer."""
        text = evaluate_on_main_thread(element.get_text)
        matches = find_locations(text)
        return self._resolve(matches[-1], "the last match in the buffer") \
            if matches else None

    def _from_caret_line(self, element):
        """Rung 2: the line the caret is on."""
        line = evaluate_on_main_thread(element.get_line_at_caret)
        matches = find_locations(line)
        return self._resolve(matches[-1], "the caret line") if matches else None

    def _from_selection(self, element):
        """Rung 3: whatever the human selected."""
        selection = evaluate_on_main_thread(element.get_selection)
        matches = find_locations(selection)
        return self._resolve(matches[-1], "the selection") if matches else None

    @staticmethod
    def _resolve(match, how):
        path = pathlib.Path(match.group("path")).expanduser()
        return str(path), int(match.group("line")), how


if __name__ == "__main__":
    sys.exit(run_action(JumpToError(dry_run="--dry-run" in sys.argv)))
