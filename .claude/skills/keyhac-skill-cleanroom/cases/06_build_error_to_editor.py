"""Error line to editor: the text layer, both error shapes, and a key for it."""

import outcome

TITLE = "One key from a failed build to the line that broke it"
#: A terminal showing a build that has already failed: a linter warning in
#: `path:line:col` form first, then a Python traceback whose last frame -
#: src/parser.py, line 42 - is the one that raised.
SCREEN = {"terminal": ("build_failure.sh", "build - acme")}
EDITS_CONFIG = True
PROMPT = """\
When a build fails in my terminal I want one key that opens the file and line
that broke it in VS Code (code -g file:line). There's a failed build in the
terminal window right now - try it on that, but don't actually open the editor
while you're testing, just show me what it would open. Then put it on a key
that's free.
"""
MUST = """\
- Run against the window that is up, it names src/parser.py line 42: the
  frame that raised, the last match on screen rather than the first.
- It knows both shapes on screen - `path:line:col` and Python's
  `File "...", line N` - by pattern, not by asking a model.
- It reads the terminal's whole text and needs no selection.
- config.py gains the import and one binding on a key nothing else uses, and
  nothing else in it changes; the reply names the key and asks the user to
  press it.
Must not: call an LLM to find the line; bind over one of the user's keys.
"""


def check(o):
    failures = []
    # The action's own answer, as Keyhac handed it back - the terminal's text
    # says "parser.py", line 42 too, so a read of the screen does not count.
    if not (o.keyhac_said(r"parser\.py\W{0,12}(line\s*)?42\b", "get_action_result")
            or o.keyhac_said(r"parser\.py\W{0,12}(line\s*)?42\b", "start_action")):
        failures.append("no run of the action reported src/parser.py line 42")
    actions = o.actions()
    if not actions:
        return failures + ["no action was written to extensions/"]
    added, _lost = outcome.binding_changes(o)
    bound = [(key, action) for _where, key, action in added
             if action.split(".")[0] in actions]
    if not bound:
        failures.append("config.py binds no key to the action it wrote")
    failures += outcome.lost_bindings(o)
    return failures


ACTION = '''\
import re
from keyhac import *

LAST = re.compile(r'File "([^"]+)", line (\\d+)|^(\\S+?):(\\d+):', re.MULTILINE)


class JumpToError(ThreadedAction):
    def __init__(self, dry_run=False):
        self.dry_run = dry_run

    def run(self):
        return None
'''


def reference(o):
    o.extensions["jump_to_error.py"] = ACTION
    o.config_after = o.config_before.replace(
        "    # ---- An action written for you by an agent",
        "    import jump_to_error\n"
        "    kt[f\"{MOD1}-E\"] = jump_to_error.JumpToError()\n\n"
        "    # ---- An action written for you by an agent", 1)
    o.calls = [
        outcome.ToolCall("mcp__keyhac__start_action", {"name": "JumpToError"}),
        outcome.ToolCall("mcp__keyhac__get_action_result", {},
                         result="finished: would open /tmp/acme/src/parser.py:42"),
        outcome.ToolCall("mcp__keyhac__write_config", {}),
        outcome.ToolCall("mcp__keyhac__reload_config", {}, result="config reloaded"),
    ]
    return o
